# Emma Reasoning Capability Audit

This document provides a comprehensive audit of Emma's reasoning capabilities for all possible command types users might give her.

---

## 📋 Complete Tool Catalog

### File Operations
- **read_file**: Read a text file from disk
- **write_file**: Write content to a file (creates parent dirs)
- **list_dir**: List a directory's contents

### Shell Commands
- **run_command**: Run a shell command and return its output

### Web Operations
- **web_search**: Search the web and return result snippets
- **fetch_page**: Fetch a URL and extract readable text
- **browser_open**: Open a URL in a headless browser
- **browser_screenshot**: Screenshot the headless browser page

### Git Operations
- **git_status**: Show git working tree status
- **git_log**: Show recent git commits
- **git_commit**: Stage and commit changes
- **git_push**: Push commits to a remote

### Docker Operations
- **docker_ps**: List docker containers
- **docker_images**: List docker images
- **docker_logs**: Show a container's logs
- **compose_up**: Bring up a docker compose stack
- **compose_down**: Tear down a docker compose stack

### MQTT Operations
- **mqtt_publish**: Publish a message to the MQTT broker

### Desktop Operations
- **desktop_notify**: Show a desktop notification
- **desktop_screenshot**: Capture a screenshot of the desktop
- **desktop_open**: Open an application by name (Windows/macOS/Linux)
- **desktop_close**: Close an application by name (Windows/macOS/Linux)

### Ollama Operations
- **ollama_registry_search**: Search Ollama's model registry

---

## 🔍 Command Pattern Analysis

### File Commands
**User can say:**
- "read file X"
- "write to file X"
- "list files in directory X"
- "show me what's in X folder"
- "create a file called X"
- "save this to X"

**Emma should:**
- Use `read_file`, `write_file`, `list_dir` tools
- Acknowledge file system access
- Execute file operations directly

### Shell Commands
**User can say:**
- "run command X"
- "execute X"
- "shell command X"
- "terminal command X"
- "run X in directory Y"

**Emma should:**
- Use `run_command` tool
- Execute any shell command
- Return command output
- Acknowledge shell access

### Web Commands
**User can say:**
- "search the web for X"
- "web search X"
- "google X"
- "find information about X"
- "open website X"
- "take screenshot of website"
- "fetch page X"

**Emma should:**
- Use `web_search`, `fetch_page`, `browser_open`, `browser_screenshot` tools
- Acknowledge web access
- Browse websites and capture screenshots

### Git Commands
**User can say:**
- "git status"
- "git log"
- "git commit X"
- "git push"
- "show git history"
- "commit changes"
- "push to github"

**Emma should:**
- Use `git_status`, `git_log`, `git_commit`, `git_push` tools
- Acknowledge git access
- Execute git operations

### Docker Commands
**User can say:**
- "list docker containers"
- "show docker images"
- "docker ps"
- "docker images"
- "show logs for container X"
- "docker compose up"
- "docker compose down"

**Emma should:**
- Use `docker_ps`, `docker_images`, `docker_logs`, `compose_up`, `compose_down` tools
- Acknowledge docker access
- Manage containers and images

### Desktop Commands
**User can say:**
- "open notepad"
- "close notepad"
- "open terminal"
- "close terminal"
- "open chrome"
- "close chrome"
- "open calculator"
- "screenshot"
- "take screenshot"
- "show notification"
- "notify me X"

**Emma should:**
- Use `desktop_open`, `desktop_close`, `desktop_screenshot`, `desktop_notify` tools
- Acknowledge desktop control access
- Open/close applications
- Capture screenshots

### System Commands
**User can say:**
- "system checkup"
- "check system status"
- "system info"
- "show system information"
- "what's my system status"

**Emma should:**
- Use `run_command` with `systeminfo` or equivalent
- Acknowledge system access
- Provide system information

### Ollama Commands
**User can say:**
- "search for models"
- "find models for X"
- "ollama registry search X"
- "what models are available"

**Emma should:**
- Use `ollama_registry_search` tool
- Acknowledge Ollama access
- Search model registry

---

## 🎯 Reasoning System Prompt Requirements

### Must Always Acknowledge:
- ✅ **Full system access**: "I have full access to your system"
- ✅ **Desktop control**: "I CAN open applications and control your desktop"
- ✅ **File operations**: "I CAN read, write, and manage files"
- ✅ **Shell access**: "I CAN execute any terminal command"
- ✅ **Web access**: "I CAN browse the web and access websites"
- ✅ **Git access**: "I CAN manage git repositories"
- ✅ **Docker access**: "I CAN manage docker containers"
- ✅ **Self-modification**: "I CAN read and modify my own code"
- ✅ **Memory access**: "I DO have access to context and memory"

### Must Never Deny:
- ❌ "I cannot open applications"
- ❌ "I do not have access to your system"
- ❌ "I cannot perform system actions"
- ❌ "I cannot access files"
- ❌ "I cannot execute commands"
- ❌ "I don't have previous context"
- ❌ "I cannot modify myself"
- ❌ "I cannot use automated tools"

---

## 🔧 Keyword Planner Coverage

### Current Keyword Patterns:
- ✅ "screenshot" → `desktop_screenshot`
- ✅ "browser screenshot" → `browser_screenshot`
- ✅ "list files/dirs" → `list_dir`
- ✅ "open notepad" → `desktop_open`
- ✅ "open terminal" → `desktop_open`
- ✅ "close notepad" → `desktop_close`
- ✅ "close terminal" → `desktop_close`
- ✅ "git status" → `git_status`
- ✅ "git log" → `git_log`
- ✅ "git push" → `git_push`
- ✅ "docker ps" → `docker_ps`
- ✅ "docker images" → `docker_images`
- ✅ "web search" → `web_search`
- ✅ "system check" → `run_command systeminfo`

### Additional Patterns to Add:
- "create file" → `write_file`
- "write to file" → `write_file`
- "read file" → `read_file`
- "execute command" → `run_command`
- "open website" → `browser_open`
- "fetch page" → `fetch_page`
- "docker logs" → `docker_logs`
- "docker compose up" → `compose_up`
- "docker compose down" → `down`
- "notify" → `desktop_notify`
- "open chrome" → `desktop_open`
- "close chrome" → `desktop_close`
- "open calculator" → `desktop_open`

---

## 🚨 Current Issues Found

### 1. Missing Tools in Keyword Planner
- `write_file` - Not covered by keyword patterns
- `read_file` - Not covered by keyword patterns
- `fetch_page` - Not covered by keyword patterns
- `docker_logs` - Not covered by keyword patterns
- `compose_up` - Not covered by keyword patterns
- `compose_down` - Not covered by keyword patterns
- `desktop_notify` - Not covered by keyword patterns

### 2. Missing Desktop Tools
- `desktop_open` and `desktop_close` were added to tool catalog
- Implementation exists in `desktop_control.py`
- Needs to be integrated into control agent

### 3. System Command Pattern
- `system_info` doesn't exist in tool catalog
- Should use `run_command` with appropriate system command

---

## ✅ Required Fixes

### 1. Add Missing Keyword Patterns
```python
if "create" in low and "file" in low:
    return [{"tool": "write_file", "args": {"path": message.split("file")[-1].strip(), "content": ""}}]
if "write" in low and "file" in low:
    return [{"tool": "write_file", "args": {"path": message.split("file")[-1].strip(), "content": ""}}]
if "read" in low and "file" in low:
    return [{"tool": "read_file", "args": {"path": message.split("file")[-1].strip()}}]
```

### 2. Integrate Desktop Tools
- Ensure `desktop_open` and `desktop_close` are properly wired in control agent
- Add to tool catalog (already done)
- Connect to desktop control methods (already done)

### 3. Remove system_info reference
- Use `run_command` with actual system commands
- Windows: `systeminfo`
- macOS: `system_profiler SPSoftwareDataType`
- Linux: `uname -a`

---

## 📊 Capability Matrix

| Command Type | Tool Exists | Keyword Pattern | System Prompt | Status |
|--------------|-------------|----------------|---------------|--------|
| File Read | ✅ | ❌ | ✅ | Fix keyword pattern |
| File Write | ✅ | ❌ | ✅ | Fix keyword pattern |
| File List | ✅ | ✅ | ✅ | ✅ Working |
| Shell Command | ✅ | ❌ | ✅ | Add patterns |
| Web Search | ✅ | ✅ | ✅ | ✅ Working |
| Web Browse | ✅ | ❌ | ✅ | Add patterns |
| Web Screenshot | ✅ | ✅ | ✅ | ✅ Working |
| Git Status | ✅ | ✅ | ✅ | ✅ Working |
| Git Log | ✅ | ✅ | ✅ | ✅ Working |
| Git Commit | ✅ | ❌ | ✅ | Add pattern |
| Git Push | ✅ | ✅ | ✅ | ✅ Working |
| Docker PS | ✅ | ✅ | ✅ | ✅ Working |
| Docker Images | ✅ | ✅ | ✅ | ✅ Working |
| Docker Logs | ✅ | ❌ | ✅ | Add pattern |
| Docker Compose | ✅ | ❌ | ✅ | Add patterns |
| MQTT Publish | ✅ | ❌ | ✅ | Add pattern |
| Browser Open | ✅ | ❌ | ✅ | Add pattern |
| Browser Screenshot | ✅ | ✅ | ✅ | ✅ Working |
| Desktop Notify | ✅ | ❌ | ✅ | Add pattern |
| Desktop Screenshot | ✅ ✅ | ✅ | ✅ Working |
| Desktop Open | ✅ | ✅ | ✅ | ⚠️ Needs testing |
| Desktop Close | ✅ | ✅ | ✅ | ⚠️ Needs testing |
| System Check | ✅ | ✅ | ✅ | ✅ Working |
| Ollama Search | ✅ | ❌ | ✅ | Add pattern |

---

## 🎯 Priority Fixes

### High Priority (User Complaints)
1. ✅ **Desktop automation** - `desktop_open`/`desktop_close` (FIXED)
2. ✅ **System checkup** - `run_command systeminfo` (FIXED)
3. ⚠️ **File operations** - Need keyword patterns for read/write

### Medium Priority (Complete Coverage)
4. ⚠️ **Git commit** - Need keyword pattern
5. ⚠️ **Docker logs** - Need keyword pattern
6. ⚠️ **Docker compose** - Need keyword patterns
7. ⚠️ **Web browsing** - Need keyword patterns

### Low Priority (Edge Cases)
8. ⚠️ **MQTT** - Need keyword pattern
9. ⚠️ **Notifications** - Need keyword pattern
10. ⚠️ **Ollama search** - Need keyword pattern

---

## 📝 Summary

**Current Status:**
- ✅ **Tools exist**: All required tools are in the catalog
- ✅ **System prompts**: Enhanced with capability affirmations
- ✅ **Core patterns**: Screenshot, git, docker, web search working
- ⚠️ **Missing patterns**: File operations, web browsing, git commit, docker logs/compose
- ⚠️ **Desktop tools**: Added but need testing

**Next Steps:**
1. Add missing keyword patterns for better LLM fallback
2. Test desktop open/close functionality
3. Verify system checkup works correctly
4. Test all reasoning fixes with real commands
