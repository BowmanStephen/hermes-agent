> **STATUS (2026-09-09):** Point-in-time 2026-06-30 audit; issues resolved by the Aug rebuild (see ops/handoffs/). Historical.

Gap Audit Delta Table Analysis:

🔴 CRITICAL DRIFT — Fix Now
Config Key	Source Value	MacBook	Mac Mini	Action

auxiliary.curator.provider	gemini	gemini ✅	ollama-cloud ❌	Pin to gemini
auxiliary.curator.model	gemini-3-flash-preview	gemini-3-flash-preview ✅	deepseek-v4-pro ❌	Pin to gemini-3-flash-preview
auxiliary.title_generation.model	gemini-2.5-flash	gemini-2.5-flash ✅	gemini-3-flash-preview ⚠️	Match to gemini-2.5-flash
delegation.max_concurrent_children	4 (fleet std)	6 ⚠️	4 ✅	Set MacBook to 4
delegation.max_spawn_depth	2 (fleet std)	2 ✅	1 ⚠️	Set Mac Mini to 2
auxiliary.monitor.model	gemini-3-flash-preview	(empty) ⚠️	gemini-3-flash-preview ✅	Pin MacBook monitor model
auxiliary.tts_audio_tags.model	gemini-3-flash-preview	(empty) ⚠️	gemini-3-flash-preview ✅	Pin MacBook tts_audio_tags model
pre_update_backup	true	None ❌	None ❌	Enable on both
tirith_fail_open	false	None ❌	None ❌	Set to false on both
destructive_slash_confirm	true	None ❌	None ❌	Enable on both
write_json_snapshots	true	None ❌ (sessions has it)	None ❌	Enable at top level
voice.auto_tts	true	False ❌	False ❌	Enable on both

🟡 DORMANT — Worth Evaluating
Feature	Source	Fleet Status	Recommendation
Kanban board	Masterclass 7 + standalone video	Not configured	Activate — we have 7 profiles already, Kanban is the natural multi-agent collaboration layer
MOA (Mixture of Agents)	Masterclass 5	moa_aggregator + moa_reference configured but unused	Investigate — aux slots are pinned but no MOA preset is active
Wake-agent gates	Masterclass 7 cron video	Not used	Activate — cron monitoring jobs could poll cheaply and only wake the model when something changes
Dashboard themes	Web dashboard video	Not configured	Skip — cosmetic, desktop app is primary UI
Custom plugins	Web dashboard video	None built	Skip — no current need
HyperFrames	Standalone video	Not configured	Skip — video generation already active via FAL
P5.js / Manim skills	Creative visualization video	Not installed	Activate — P5.js for pool dashboard data viz is a good fit

✅ ACTIVE — No Action
All 16 auxiliary slots pinned to gemini (MacBook) ✅
Delegation pinned to gemini-3-flash-preview ✅
Fallback chain: free Nous → ollama-cloud → deepseek-v4-pro last ✅
Safety: hard_stop_guardrails: true on both ✅
STT: local faster-whisper ✅
TTS: Kokoro ✅
max_concurrent_sessions: 0 (no warning spam) ✅
