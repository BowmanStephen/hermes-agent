"""Gateway session expiry watcher service."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("gateway.run")


class SessionExpiryWatcher:
    def __init__(self, runner: Any, pending_sentinel: Any) -> None:
        object.__setattr__(self, "_runner", runner)
        object.__setattr__(self, "_pending_sentinel", pending_sentinel)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_runner", "_pending_sentinel"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._runner, name, value)

    async def _session_expiry_watcher(self, interval: int = 300):
        """Background task that finalizes expired sessions.

        Runs every ``interval`` seconds (default 5 min).  For each session
        whose reset policy has expired, invokes ``on_session_finalize``
        hooks, cleans up the cached AIAgent's tool resources, evicts the
        cache entry so it can be garbage-collected, and marks the session
        so it won't be finalized again.
        """
        await asyncio.sleep(60)  # initial delay — let the gateway fully start
        _finalize_failures: dict[str, int] = {}  # session_id -> consecutive failure count
        _MAX_FINALIZE_RETRIES = 3
        while self._running:
            try:
                self.session_store._ensure_loaded()
                # Collect expired sessions first, then log a single summary.
                _expired_entries = []
                for key, entry in list(self.session_store._entries.items()):
                    if entry.expiry_finalized:
                        continue
                    if not self.session_store._is_session_expired(entry):
                        continue
                    _expired_entries.append((key, entry))

                if _expired_entries:
                    # Extract platform names from session keys for a compact summary.
                    # Keys look like "agent:main:telegram:dm:12345" — platform is field [2].
                    _platforms: dict[str, int] = {}
                    for _k, _e in _expired_entries:
                        _parts = _k.split(":")
                        _plat = _parts[2] if len(_parts) > 2 else "unknown"
                        _platforms[_plat] = _platforms.get(_plat, 0) + 1
                    _plat_summary = ", ".join(
                        f"{p}:{c}" for p, c in sorted(_platforms.items())
                    )
                    logger.info(
                        "Session expiry: %d sessions to finalize (%s)",
                        len(_expired_entries), _plat_summary,
                    )

                for key, entry in _expired_entries:
                    try:
                        try:
                            from hermes_cli.plugins import invoke_hook as _invoke_hook
                            _parts = key.split(":")
                            _platform = _parts[2] if len(_parts) > 2 else ""
                            _invoke_hook(
                                "on_session_finalize",
                                session_id=entry.session_id,
                                platform=_platform,
                            )
                        except Exception:
                            pass
                        # Shut down memory provider and close tool resources
                        # on the cached agent.  Idle agents live in
                        # _agent_cache (not _running_agents), so look there.
                        _cached_agent = None
                        _cache_lock = getattr(self, "_agent_cache_lock", None)
                        if _cache_lock is not None:
                            with _cache_lock:
                                _cached = self._agent_cache.get(key)
                                _cached_agent = _cached[0] if isinstance(_cached, tuple) else _cached if _cached else None
                        # Fall back to _running_agents in case the agent is
                        # still mid-turn when the expiry fires.
                        if _cached_agent is None:
                            _cached_agent = self._running_agents.get(key)
                        if _cached_agent and _cached_agent is not self._pending_sentinel:
                            self._cleanup_agent_resources(_cached_agent)
                        # Drop the cache entry so the AIAgent (and its LLM
                        # clients, tool schemas, memory provider refs) can
                        # be garbage-collected.  Otherwise the cache grows
                        # unbounded across the gateway's lifetime.
                        self._evict_cached_agent(key)
                        # Mark as finalized and persist to disk so the flag
                        # survives gateway restarts.
                        with self.session_store._lock:
                            entry.expiry_finalized = True
                            self.session_store._save()
                        logger.debug(
                            "Session expiry finalized for %s",
                            entry.session_id,
                        )
                        _finalize_failures.pop(entry.session_id, None)
                    except Exception as e:
                        failures = _finalize_failures.get(entry.session_id, 0) + 1
                        _finalize_failures[entry.session_id] = failures
                        if failures >= _MAX_FINALIZE_RETRIES:
                            logger.warning(
                                "Session finalize gave up after %d attempts for %s: %s. "
                                "Marking as finalized to prevent infinite retry loop.",
                                failures, entry.session_id, e,
                            )
                            with self.session_store._lock:
                                entry.expiry_finalized = True
                                self.session_store._save()
                            _finalize_failures.pop(entry.session_id, None)
                        else:
                            logger.debug(
                                "Session finalize failed (%d/%d) for %s: %s",
                                failures, _MAX_FINALIZE_RETRIES, entry.session_id, e,
                            )

                if _expired_entries:
                    _done = sum(
                        1 for _, e in _expired_entries if e.expiry_finalized
                    )
                    _failed = len(_expired_entries) - _done
                    if _failed:
                        logger.info(
                            "Session expiry done: %d finalized, %d pending retry",
                            _done, _failed,
                        )
                    else:
                        logger.info(
                            "Session expiry done: %d finalized", _done,
                        )

                # Sweep agents that have been idle beyond the TTL regardless
                # of session reset policy.  This catches sessions with very
                # long / "never" reset windows, whose cached AIAgents would
                # otherwise pin memory for the gateway's entire lifetime.
                try:
                    _idle_evicted = self._sweep_idle_cached_agents()
                    if _idle_evicted:
                        logger.info(
                            "Agent cache idle sweep: evicted %d agent(s)",
                            _idle_evicted,
                        )
                except Exception as _e:
                    logger.debug("Idle agent sweep failed: %s", _e)

                # Periodically prune stale SessionStore entries.  The
                # in-memory dict (and sessions.json) would otherwise grow
                # unbounded in gateways serving many rotating chats /
                # threads / users over long time windows.  Pruning is
                # invisible to users — a resumed session just gets a
                # fresh session_id, exactly as if the reset policy fired.
                _last_prune_ts = getattr(self, "_last_session_store_prune_ts", 0.0)
                _prune_interval = 3600.0  # once per hour
                if time.time() - _last_prune_ts > _prune_interval:
                    try:
                        _max_age = int(
                            getattr(self.config, "session_store_max_age_days", 0) or 0
                        )
                        if _max_age > 0:
                            _pruned = self.session_store.prune_old_entries(_max_age)
                            if _pruned:
                                logger.info(
                                    "SessionStore prune: dropped %d stale entries",
                                    _pruned,
                                )
                    except Exception as _e:
                        logger.debug("SessionStore prune failed: %s", _e)
                    self._last_session_store_prune_ts = time.time()
            except Exception as e:
                logger.debug("Session expiry watcher error: %s", e)
            # Sleep in small increments so we can stop quickly
            for _ in range(interval):
                if not self._running:
                    break
                await asyncio.sleep(1)
