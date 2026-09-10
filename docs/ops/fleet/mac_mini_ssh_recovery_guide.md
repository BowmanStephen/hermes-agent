# Mac Mini SSH Recovery Guide

## Current Issue
Cannot SSH to Mac Mini (stephens-mac-mini-1 / 100.125.187.18) due to "Permission denied (publickey,keyboard-interactive)" error.

## Diagnosis Summary
1. Mac Mini is reachable on network (ping and port 22 are open)
2. SSH authentication is failing due to key issues
3. 1Password SSH agent is likely interfering with authentication
4. Current SSH keys are not properly configured for Mac Mini access

## Recovery Steps

### Step 1: Disable SSH Agent Interference
```bash
# Temporarily disable SSH agent
unset SSH_AUTH_SOCK

# Or disable 1Password SSH agent specifically
# (This depends on your 1Password setup)
```

### Step 2: Generate New SSH Key Pair
```bash
# Generate a new key pair specifically for Mac Mini access
ssh-keygen -t ed25519 -f ~/.ssh/id_hermes_mini -C "hermes-macmini-access" -N ""

# This creates:
# - ~/.ssh/id_hermes_mini (private key)
# - ~/.ssh/id_hermes_mini.pub (public key)
```

### Step 3: Copy Public Key to Mac Mini
Since we can't SSH directly, we need an alternative method:

#### Option A: Physical Access
1. Connect a keyboard and mouse to Mac Mini
2. Log in locally
3. Open Terminal on Mac Mini
4. Create the SSH directory if it doesn't exist:
   ```bash
   mkdir -p ~/.ssh
   chmod 700 ~/.ssh
   ```
5. Add the public key to authorized_keys:
   ```bash
   # Copy the contents of ~/.ssh/id_hermes_mini.pub from MacBook
   echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIImQ61PWvJcp6IHU6xIBlvNcDmDfSgE3LqcDq5IcIk/u hermes-macmini-access" >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```

#### Option B: Screen Sharing
1. Enable Screen Sharing on Mac Mini (if accessible via System Preferences)
2. Connect via Screen Sharing from MacBook
3. Follow the same steps as Physical Access

#### Option C: Network File Sharing
1. Enable File Sharing on Mac Mini
2. Mount the Mac Mini's home directory on MacBook
3. Manually copy the public key to the appropriate location

### Step 4: Configure SSH Client
Add to `~/.ssh/config` on MacBook:
```
Host mac-mini
    HostName 100.125.187.18
    User stephenbowman
    IdentityFile ~/.ssh/id_hermes_mini
    IdentitiesOnly yes
    ServerAliveInterval 60
```

### Step 5: Test Connection
```bash
# Test with explicit key
ssh -o IdentitiesOnly=yes -i ~/.ssh/id_hermes_mini stephenbowman@100.125.187.18

# Or using the SSH config
ssh mac-mini
```

## Alternative Access Methods

### Tailscale SSH (if available)
If Tailscale SSH is properly configured:
```bash
tailscale ssh stephenbowman@stephens-mac-mini-1
```

### Remote Management Tools
1. **Apple Remote Desktop** (if enabled)
2. **TeamViewer** or similar remote desktop tools
3. **Chrome Remote Desktop** (if set up)

## Mac Mini Configuration Verification

Once access is restored, verify and correct configurations:

### Check Current Configuration
```bash
# On Mac Mini
cat ~/.hermes/config.yaml | grep -A 5 -B 5 "delegation"
cat ~/.hermes/config.yaml | grep -A 5 -B 5 "auxiliary"
```

### Sync Configuration with MacBook
Ensure these values match across fleet:
```yaml
delegation:
  max_spawn_depth: 2
  max_concurrent_children: 4

auxiliary:
  curator:
    provider: gemini
    model: gemini-3-flash-preview
  title_generation:
    model: gemini-2.5-flash
  monitor:
    model: gemini-3-flash-preview
  tts_audio_tags:
    model: gemini-3-flash-preview

# Security settings
updates:
  pre_update_backup: true
security:
  tirith_fail_open: false
approvals:
  destructive_slash_confirm: true
sessions:
  write_json_snapshots: true
voice:
  auto_tts: true
```

## Fleet Health Monitoring

### Create Connectivity Check Script
```bash
#!/bin/bash
# ~/bin/check_fleet.sh

echo "Checking Hermes Fleet Connectivity..."

# MacBook (localhost)
echo "✓ MacBook: localhost"

# Mac Mini
if ping -c 1 100.125.187.18 &>/dev/null; then
    echo "✓ Mac Mini: reachable"
else
    echo "✗ Mac Mini: unreachable"
fi

# VPS
if ping -c 1 100.104.166.64 &>/dev/null; then
    echo "✓ VPS: reachable"
else
    echo "✗ VPS: unreachable"
fi
```

### Set Up Cron Job
```bash
# Add to crontab
*/30 * * * * ~/bin/check_fleet.sh >> ~/logs/fleet_health.log 2>&1
```

## Prevention and Best Practices

### SSH Key Management
1. Use separate keys for different purposes
2. Document key usage
3. Regularly rotate keys
4. Use SSH config files for consistent connections

### Configuration Management
1. Version control all configurations
2. Use templates for consistent deployment
3. Regular configuration audits
4. Automated sync scripts

### Documentation
1. Maintain up-to-date fleet documentation
2. Document recovery procedures
3. Keep contact information for physical access
4. Maintain a runbook for common issues

## Emergency Procedures

### If All SSH Access Is Lost
1. Physical access to Mac Mini
2. Boot from external drive
3. Reset user account if necessary
4. Reinstall Hermes if needed

### Quick Recovery Commands
```bash
# Check SSH service status (on Mac Mini)
sudo launchctl list | grep ssh

# Restart SSH service (on Mac Mini)
sudo launchctl unload /System/Library/LaunchDaemons/ssh.plist
sudo launchctl load /System/Library/LaunchDaemons/ssh.plist

# Check SSH configuration (on Mac Mini)
sudo sshd -T | grep -E "(permitrootlogin|passwordauthentication|pubkeyauthentication)"
```

## Next Steps

1. Gain access to Mac Mini using one of the methods above
2. Verify current configuration
3. Sync configurations across fleet
4. Implement monitoring and alerting
5. Document the recovery process for future reference

## Quick Workaround (from ssh_recovery_plan.md, merged 2026-09-09)

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 stephenbowman@100.125.187.18
```

Bypasses 1Password SSH agent offering too many keys ("Too many authentication failures").