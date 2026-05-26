"""Operations usage and diagnostics command handlers."""
from __future__ import annotations

from ._imports import *  # noqa: F403
from .base import RunnerBackedCommandService

class OperationsUsageGatewayCommands(RunnerBackedCommandService):
    async def _handle_usage_command(self, event: MessageEvent) -> str:
        """Handle /usage command -- show token usage for the current session.

        Checks both _running_agents (mid-turn) and _agent_cache (between turns)
        so that rate limits, cost estimates, and detailed token breakdowns are
        available whenever the user asks, not only while the agent is running.
        """
        source = event.source
        session_key = self._session_key_for_source(source)

        # Try running agent first (mid-turn), then cached agent (between turns)
        agent = self._running_agents.get(session_key)
        if not agent or agent is _AGENT_PENDING_SENTINEL:
            _cache_lock = getattr(self, "_agent_cache_lock", None)
            _cache = getattr(self, "_agent_cache", None)
            if _cache_lock and _cache is not None:
                with _cache_lock:
                    cached = _cache.get(session_key)
                    if cached:
                        agent = cached[0]

        # Resolve provider/base_url/api_key for the account-usage fetch.
        # Prefer the live agent; fall back to persisted billing data on the
        # SessionDB row so `/usage` still returns account info between turns
        # when no agent is resident.
        provider = getattr(agent, "provider", None) if agent and agent is not _AGENT_PENDING_SENTINEL else None
        base_url = getattr(agent, "base_url", None) if agent and agent is not _AGENT_PENDING_SENTINEL else None
        api_key = getattr(agent, "api_key", None) if agent and agent is not _AGENT_PENDING_SENTINEL else None
        if not provider and getattr(self, "_session_db", None) is not None:
            try:
                _entry_for_billing = self.session_store.get_or_create_session(source)
                persisted = self._session_db.get_session(_entry_for_billing.session_id) or {}
            except Exception:
                persisted = {}
            provider = provider or persisted.get("billing_provider")
            base_url = base_url or persisted.get("billing_base_url")

        # Fetch account usage off the event loop so slow provider APIs don't
        # block the gateway. Failures are non-fatal -- account_lines stays [].
        account_lines: list[str] = []
        if provider:
            try:
                account_snapshot = await asyncio.to_thread(
                    fetch_account_usage,
                    provider,
                    base_url=base_url,
                    api_key=api_key,
                )
            except Exception:
                account_snapshot = None
            if account_snapshot:
                account_lines = render_account_usage_lines(account_snapshot, markdown=True)

        if agent and hasattr(agent, "session_total_tokens") and agent.session_api_calls > 0:
            lines = []

            # Rate limits (when available from provider headers)
            rl_state = agent.get_rate_limit_state()
            if rl_state and rl_state.has_data:
                from agent.rate_limit_tracker import format_rate_limit_compact
                lines.append(t("gateway.usage.rate_limits", state=format_rate_limit_compact(rl_state)))
                lines.append("")

            # Session token usage — detailed breakdown matching CLI
            input_tokens = getattr(agent, "session_input_tokens", 0) or 0
            output_tokens = getattr(agent, "session_output_tokens", 0) or 0
            cache_read = getattr(agent, "session_cache_read_tokens", 0) or 0
            cache_write = getattr(agent, "session_cache_write_tokens", 0) or 0

            lines.append(t("gateway.usage.header_session"))
            lines.append(t("gateway.usage.label_model", model=agent.model))
            lines.append(t("gateway.usage.label_input_tokens", count=f"{input_tokens:,}"))
            if cache_read:
                lines.append(t("gateway.usage.label_cache_read", count=f"{cache_read:,}"))
            if cache_write:
                lines.append(t("gateway.usage.label_cache_write", count=f"{cache_write:,}"))
            lines.append(t("gateway.usage.label_output_tokens", count=f"{output_tokens:,}"))
            lines.append(t("gateway.usage.label_total", count=f"{agent.session_total_tokens:,}"))
            lines.append(t("gateway.usage.label_api_calls", count=agent.session_api_calls))

            # Cost estimation
            try:
                from agent.usage_pricing import CanonicalUsage, estimate_usage_cost
                cost_result = estimate_usage_cost(
                    agent.model,
                    CanonicalUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read,
                        cache_write_tokens=cache_write,
                    ),
                    provider=getattr(agent, "provider", None),
                    base_url=getattr(agent, "base_url", None),
                )
                if cost_result.amount_usd is not None:
                    prefix = "~" if cost_result.status == "estimated" else ""
                    lines.append(t("gateway.usage.label_cost", prefix=prefix, amount=f"{float(cost_result.amount_usd):.4f}"))
                elif cost_result.status == "included":
                    lines.append(t("gateway.usage.label_cost_included"))
            except Exception:
                pass

            # Context window and compressions
            ctx = agent.context_compressor
            if ctx.last_prompt_tokens:
                pct = min(100, ctx.last_prompt_tokens / ctx.context_length * 100) if ctx.context_length else 0
                lines.append(t("gateway.usage.label_context", used=f"{ctx.last_prompt_tokens:,}", total=f"{ctx.context_length:,}", pct=f"{pct:.0f}"))
            if ctx.compression_count:
                lines.append(t("gateway.usage.label_compressions", count=ctx.compression_count))

            if account_lines:
                lines.append("")
                lines.extend(account_lines)

            return "\n".join(lines)

        # No agent at all -- check session history for a rough count
        session_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(session_entry.session_id)
        if history:
            from agent.model_metadata import estimate_messages_tokens_rough
            msgs = [m for m in history if m.get("role") in {"user", "assistant"} and m.get("content")]
            approx = estimate_messages_tokens_rough(msgs)
            lines = [
                t("gateway.usage.header_session_info"),
                t("gateway.usage.label_messages", count=len(msgs)),
                t("gateway.usage.label_estimated_context", count=f"{approx:,}"),
                t("gateway.usage.detailed_after_first"),
            ]
            if account_lines:
                lines.append("")
                lines.extend(account_lines)
            return "\n".join(lines)
        if account_lines:
            return "\n".join(account_lines)
        return t("gateway.usage.no_data")

    async def _handle_insights_command(self, event: MessageEvent) -> str:
        """Handle /insights command -- show usage insights and analytics."""
        args = event.get_command_args().strip()

        # Normalize Unicode dashes (Telegram/iOS auto-converts -- to em/en dash)
        args = re.sub(r'[\u2012\u2013\u2014\u2015](days|source)', r'--\1', args)

        days = 30
        source = None

        # Parse simple args: /insights 7  or  /insights --days 7
        if args:
            parts = args.split()
            i = 0
            while i < len(parts):
                if parts[i] == "--days" and i + 1 < len(parts):
                    try:
                        days = int(parts[i + 1])
                    except ValueError:
                        return t("gateway.insights.invalid_days", value=parts[i + 1])
                    i += 2
                elif parts[i] == "--source" and i + 1 < len(parts):
                    source = parts[i + 1]
                    i += 2
                elif parts[i].isdigit():
                    days = int(parts[i])
                    i += 1
                else:
                    i += 1

        try:
            from hermes_state import SessionDB
            from agent.insights import InsightsEngine

            loop = asyncio.get_running_loop()

            def _run_insights():
                db = SessionDB()
                engine = InsightsEngine(db)
                report = engine.generate(days=days, source=source)
                result = engine.format_gateway(report)
                db.close()
                return result

            return await loop.run_in_executor(None, _run_insights)
        except Exception as e:
            logger.error("Insights command error: %s", e, exc_info=True)
            return t("gateway.insights.error", error=e)

    async def _handle_debug_command(self, event: MessageEvent) -> str:
        """Handle /debug — upload debug report (summary only) and return paste URLs.

        Gateway uploads ONLY the summary report (system info + log tails),
        NOT full log files, to protect conversation privacy.  Users who need
        full log uploads should use ``hermes debug share`` from the CLI.
        """
        import asyncio
        from hermes_cli.debug import (
            _capture_dump, collect_debug_report,
            upload_to_pastebin, _schedule_auto_delete,
            _GATEWAY_PRIVACY_NOTICE, _best_effort_sweep_expired_pastes,
        )

        loop = asyncio.get_running_loop()

        # Run blocking I/O (dump capture, log reads, uploads) in a thread.
        def _collect_and_upload():
            _best_effort_sweep_expired_pastes()
            dump_text = _capture_dump()
            report = collect_debug_report(log_lines=200, dump_text=dump_text)

            urls = {}
            try:
                urls["Report"] = upload_to_pastebin(report)
            except Exception as exc:
                return t("gateway.debug.upload_failed", error=exc)

            # Schedule auto-deletion after 6 hours
            _schedule_auto_delete(list(urls.values()))

            lines = [_GATEWAY_PRIVACY_NOTICE, "", t("gateway.debug.header"), ""]
            label_width = max(len(k) for k in urls)
            for label, url in urls.items():
                lines.append(f"`{label:<{label_width}}`  {url}")

            lines.append("")
            lines.append(t("gateway.debug.auto_delete"))
            lines.append(t("gateway.debug.full_logs_hint"))
            lines.append(t("gateway.debug.share_hint"))
            return "\n".join(lines)

        return await loop.run_in_executor(None, _collect_and_upload)
