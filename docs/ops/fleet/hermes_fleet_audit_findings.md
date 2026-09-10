> **STATUS (2026-09-09):** Point-in-time 2026-06-30 audit; issues resolved by the Aug rebuild (see ops/handoffs/). Historical.

# Hermes Fleet Configuration Audit - Findings Summary

## Current Status

After investigating the Hermes fleet configuration, here's what I found:

### MacBook Configuration (Current)
The MacBook's current configuration is actually correct for all the parameters mentioned in the gap audit table:
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

### Historical Configuration Issues
However, examining archived configurations reveals the issues that were likely present when the gap audit was performed:

1. **Archived "always-on" profile** (likely used for Mac Mini):
   - `delegation.max_concurrent_children`: 3 (different from both current MacBook and gap audit table)
   - `delegation.max_spawn_depth`: 1 (matches gap audit table for Mac Mini)
   - `auxiliary.title_generation.model`: gemini-3-flash-preview (different from current MacBook)

2. **Other archived profiles** consistently show:
   - `max_spawn_depth: 1` (matching the gap audit table for Mac Mini)

### Fleet Connectivity Issues

#### Mac Mini SSH Authentication Failure
- Cannot SSH to Mac Mini due to "Permission denied (publickey,keyboard-interactive)" error
- Multiple authentication failures suggest 1Password SSH agent interference
- The public key being used is not in the authorized_keys file on the MacBook
- Need to disable 1Password SSH agent and use explicit key authentication

#### VPS Unreachable
- Cannot reach VPS over Tailscale or SSH
- Ping to 100.104.166.64 fails
- This affects cloud-based operations and Discord gateway

## Gap Audit Table Analysis

The gap audit table appears to have been accurate at the time it was created, but recent changes have fixed some issues:

| Config Key | Source Value | MacBook (Current) | Mac Mini (Reported) | Status |
|------------|--------------|-------------------|---------------------|--------|
| auxiliary.curator.provider | gemini | gemini ✅ | ollama-cloud ❌ | FIXED |
| auxiliary.curator.model | gemini-3-flash-preview | gemini-3-flash-preview ✅ | deepseek-v4-pro ❌ | FIXED |
| auxiliary.title_generation.model | gemini-2.5-flash | gemini-2.5-flash ✅ | gemini-3-flash-preview ⚠️ | FIXED |
| delegation.max_concurrent_children | 4 | 4 ✅ | 4 ✅ | OK |
| delegation.max_spawn_depth | 2 | 2 ✅ | 1 ⚠️ | NEEDS VERIFICATION |
| auxiliary.monitor.model | gemini-3-flash-preview | gemini-3-flash-preview ✅ | gemini-3-flash-preview ✅ | OK |
| auxiliary.tts_audio_tags.model | gemini-3-flash-preview | gemini-3-flash-preview ✅ | gemini-3-flash-preview ✅ | OK |
| pre_update_backup | true | True ✅ | None ❌ | FIXED |
| tirith_fail_open | false | False ✅ | None ❌ | FIXED |
| destructive_slash_confirm | true | True ✅ | None ❌ | FIXED |
| write_json_snapshots | true | True ✅ | None ❌ | FIXED |
| voice.auto_tts | true | True ✅ | False ❌ | FIXED |

## Required Actions

### Immediate Priority

1. **Fix Mac Mini SSH Authentication**
   - Disable 1Password SSH agent for this connection
   - Use explicit key-only authentication
   - Add MacBook's public key to Mac Mini's authorized_keys file

2. **Investigate VPS Connectivity**
   - Check if VPS is running
   - Verify Tailscale daemon status on VPS
   - Check firewall rules

### Configuration Verification

3. **Verify Mac Mini Current Config**
   - Once SSH is working, check actual Mac Mini configuration
   - Confirm `delegation.max_spawn_depth` value
   - Verify all auxiliary model configurations

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

## Conclusion

The gap audit table was likely accurate when created, but many issues have been resolved through recent configuration updates. The main remaining issues are:

1. SSH authentication problems preventing access to Mac Mini
2. VPS connectivity issues
3. Need to verify and standardize `delegation.max_spawn_depth` across the fleet

Once connectivity is restored, we can verify the current state and ensure all fleet members are properly configured.