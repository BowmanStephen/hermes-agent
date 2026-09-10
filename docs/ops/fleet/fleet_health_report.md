> **STATUS (2026-09-09):** Point-in-time 2026-06-30 audit; issues resolved by the Aug rebuild (see ops/handoffs/). Historical.

# Hermes Fleet Health Report - June 30, 2026

## Current Status

### MacBook (Primary Workstation)
- **Model**: MacBook Pro
- **Tailscale IP**: 100.95.145.37
- **Hermes Version**: v0.17.0 (config v32)
- **Status**: Fully operational

### Mac Mini (Always-on Gateway)
- **Model**: Mac Mini
- **Tailscale IP**: 100.125.187.18
- **Hostname**: stephens-mac-mini-1
- **Status**: Tailscale connectivity ✅ | SSH authentication ❌

### VPS (Cloud Gateway)
- **Status**: Tailscale connectivity ❌ | SSH connectivity ❌

## Configuration Analysis

### Gap Audit Findings
The original gap audit table showed several discrepancies between MacBook and Mac Mini configurations. Upon investigation:

1. **MacBook config is currently correct** for all parameters:
   - `auxiliary.curator.provider`: gemini ✅
   - `auxiliary.curator.model`: gemini-3-flash-preview ✅
   - `auxiliary.title_generation.model`: gemini-2.5-flash ✅
   - `delegation.max_concurrent_children`: 4 ✅
   - `delegation.max_spawn_depth`: 2 ✅
   - `auxiliary.monitor.model`: gemini-3-flash-preview ✅
   - `auxiliary.tts_audio_tags.model`: gemini-3-flash-preview ✅
   - `pre_update_backup`: True ✅
   - `tirith_fail_open`: False ✅
   - `destructive_slash_confirm`: True ✅
   - `write_json_snapshots`: True ✅
   - `voice.auto_tts`: True ✅

2. **Backup config (June 29)** shows some differences:
   - `delegation.max_spawn_depth`: 1 (matches gap audit table for Mac Mini)
   - `auxiliary.title_generation.model`: gemini-3-flash-preview (different from current)

### Fleet Issues

#### Critical: SSH Authentication Failure
- Cannot SSH to Mac Mini due to authentication issues
- Error: "Too many authentication failures"
- Likely related to 1Password SSH agent offering multiple keys
- This prevents direct config verification and fixes on Mac Mini

#### Critical: VPS Unreachable
- Cannot reach VPS over Tailscale or SSH
- Ping to 100.104.166.64 fails
- This affects cloud-based operations and Discord gateway

## Required Actions

### Immediate Priority

1. **Fix Mac Mini SSH Authentication**
   - Disable 1Password SSH agent for this connection
   - Use explicit key-only authentication
   - Command to try: `ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 stephenbowman@100.125.187.18`

2. **Investigate VPS Connectivity**
   - Check if VPS is running
   - Verify Tailscale daemon status on VPS
   - Check firewall rules

### Configuration Sync

3. **Verify Mac Mini Config**
   - Once SSH is working, verify actual Mac Mini config
   - According to gap audit table, Mac Mini had:
     - `auxiliary.curator.provider`: ollama-cloud ❌
     - `auxiliary.curator.model`: deepseek-v4-pro ❌
     - `delegation.max_spawn_depth`: 1 ⚠️ (should be 2)
   - Need to sync configs between machines

4. **Standardize Configurations**
   - Ensure all fleet machines use the same auxiliary model configurations
   - Standardize delegation settings across fleet

### Fleet Health Monitoring

5. **Implement Fleet Health Checks**
   - Create cron job to regularly check connectivity to all fleet members
   - Set up alerts for connectivity issues
   - Document recovery procedures

## Recommendations

### Short-term

1. **SSH Key Management**
   - Configure specific SSH configurations for each fleet member
   - Disable 1Password agent for fleet connections
   - Use `IdentitiesOnly=yes` in SSH config

2. **Configuration Management**
   - Create a standardized config template for fleet
   - Implement version control for configs
   - Set up automated config sync when possible

### Long-term

1. **Fleet Management Automation**
   - Use the `kb-fleet-deploy` skill once SSH is fixed
   - Implement proper configuration management
   - Set up monitoring and alerting for fleet health

2. **Documentation Updates**
   - Update fleet documentation with current status
   - Document SSH authentication issues and solutions
   - Create runbook for fleet recovery procedures

## Next Steps

1. Attempt to fix Mac Mini SSH authentication
2. Investigate VPS connectivity issues
3. Once connectivity is restored, verify and sync configurations
4. Implement monitoring for fleet health
5. Document all findings and procedures

## Additional Notes

- MacBook configuration is currently correct and aligned with best practices
- The gap audit table may have been accurate at the time but recent changes have fixed some issues
- Fleet management is currently hampered by connectivity issues
- Security settings are properly configured (tirith_fail_open=false, destructive_slash_confirm=true)