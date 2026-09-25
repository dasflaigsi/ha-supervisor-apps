#!/usr/bin/env python3
"""
Loophole Tunnel App for Home Assistant
Manages a persistent loophole tunnel to expose Home Assistant over HTTPS

This can not be run directly in Dockerfile because of missing environment variables (SUPERVISOR_TOKEN).
Dockerfile runs run.sh which fetches up the environment and then runs this script.
"""

import re
import json
import os
import subprocess
import sys
import selectors
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OPTIONS_PATH = DATA_DIR / "options.json"
LOG_PATH = DATA_DIR / "loophole.log"
ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

lock = threading.Lock()
tunnel_process = None


def get_loophole_env():
    """Return an environment that keeps Loophole state in the persistent app data volume."""
    loophole_home = DATA_DIR / "loophole-home"
    loophole_home.mkdir(parents=True, exist_ok=True)

    xdg_config = loophole_home / ".config"
    xdg_cache = loophole_home / ".cache"
    xdg_config.mkdir(parents=True, exist_ok=True)
    xdg_cache.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["HOME"] = str(loophole_home)
    env["XDG_CONFIG_HOME"] = str(xdg_config)
    env["XDG_CACHE_HOME"] = str(xdg_cache)
    return env


def log(message: str):
    """Log to stdout and logfile"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"{timestamp} {message}"
    print(log_line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(log_line + "\n")
    except Exception as e:
        print(f"Failed to write to log file: {e}", flush=True)


def load_options():
    """Load options from Home Assistant"""
    if not OPTIONS_PATH.exists():
        return {"port": 80, "hostname": "", "verbose": False, "logout_on_restart": False, "connectivity_check_interval": 15}
    try:
        return json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"Error loading options: {e}")
        return {"port": 80, "hostname": "", "verbose": False, "logout_on_restart": False, "connectivity_check_interval": 15}


def save_options(options):
    """Save options to Home Assistant"""
    try:
        OPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        OPTIONS_PATH.write_text(json.dumps(options, indent=2), encoding="utf-8")
        log(f"✓ Options saved")
    except Exception as e:
        log(f"Error saving options: {e}")


def update_option_via_api(key: str, value):
    """Update an app option via Home Assistant supervisor API"""
    try:
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            log("Warning: SUPERVISOR_TOKEN not set, cannot update options via API")
            return False
        
        headers = {
            "Authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        
        # Load current options and update only the specified key
        options = load_options()
        options[key] = value
        
        data = {"options": options}
        
        url = "http://supervisor/addons/self/options"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        response = urllib.request.urlopen(req, timeout=5)
        status = response.status
        
        if status == 200:
            log(f"✓ Option {key} updated to {value} via API")
            return True
        else:
            log(f"API returned status {status} when updating {key}")
            return False
            
    except urllib.error.HTTPError as e:
        log(f"API error {e.code} when updating option: {e.reason}")
        return False
    except urllib.error.URLError as e:
        log(f"Failed to connect to supervisor API: {e.reason}")
        return False
    except Exception as e:
        log(f"Failed to update option via API: {e}")
        return False


def is_valid(options):
    """Check if configuration is valid"""
    try:
        port = int(options.get("port", 0))
        hostname = str(options.get("hostname", "")).strip()
        return hostname and 1 <= port <= 65535
    except (TypeError, ValueError):
        return False
    

def send_notification(title: str, message: str):
    """Send notification to Home Assistant UI"""
    try:
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            log("Warning: SUPERVISOR_TOKEN not set, cannot send notification")
            return
        
        headers = {
            "Authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        data = {
            "title": title,
            "message": message,
            "notification_id": "loophole-addon-notification"
        }
        
        # Use urllib instead of requests (no external dependencies)
        url = "http://supervisor/core/api/services/persistent_notification/create"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        response = urllib.request.urlopen(req, timeout=5)
        status = response.status
        
        if status != 200:
            log(f"Notification API returned status {status}")
        else:
            log(f"✓ Notification sent: {title}")
            
    except urllib.error.HTTPError as e:
        log(f"Notification API error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        log(f"Failed to connect to supervisor API: {e.reason}")
    except Exception as e:
        log(f"Failed to send notification: {e}")

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)

def run_logout():
    """Execute loophole account logout"""
    try:
        log("Executing: loophole account logout")
        result = subprocess.run(
            ["loophole", "account", "logout"],
            capture_output=True,
            text=True,
            timeout=10,
            env=get_loophole_env(),
        )
        if result.returncode == 0:
            log("✓ Successfully logged out from Loophole")
            return True
        else:
            log(f"⚠ Logout returned code {result.returncode}: {result.stderr}")
            return False
    except Exception as e:
        log(f"Error during logout: {e}")
        return False

def run_login_check():
    """Check login status - keeps command running and captures output"""
    try:
        log("Checking Loophole authentication status...")
        
        # First verify loophole command exists
        try:
            result = subprocess.run(
                ["loophole", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                env=get_loophole_env(),
            )
            if result.returncode != 0:
                log(
                    f"❌ Loophole CLI check failed with code {result.returncode}: "
                    f"{result.stderr.strip()}"
                )
                return False
            log(f"✓ Loophole CLI available: {result.stdout.strip()}")
        except FileNotFoundError:
            log("❌ Loophole CLI not found in PATH")
            return False
        except Exception as e:
            log(f"⚠ Could not verify loophole: {e}")
            return False
        
        # Start login process - capture output from both stdout and stderr
        log("Starting: loophole account login")

        proc = subprocess.Popen(
            ["loophole", "account", "login"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=get_loophole_env(),
        )

        log(f"Login process started (PID {proc.pid})")

        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
        sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

        output = bytearray()

        start_time = time.time()
        timeout = 600

        while True:

            # Process exited?
            rc = proc.poll()
            if rc is not None:
                break

            # Timeout?
            if time.time() - start_time > timeout:
                log(f"⚠ Authentication timeout ({timeout}s)")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False

            # Wait up to 0.5s for new output
            events = sel.select(timeout=0.5)

            for key, _ in events:

                try:
                    data = os.read(key.fileobj.fileno(), 4096)
                except OSError:
                    continue

                if not data:
                    continue

                output.extend(data)

                text = data.decode(errors="replace")

                # Print immediately exactly as loophole produced it
                log(f"[{key.data}] {text}")

                if ("https://" in text or "http://" in text):
                    clean = strip_ansi(text)
                    send_notification(
                        "Loophole Tunnel - Authentication Required", 
                        f"{clean}\n\n" 
                        "The tunnel will start automatically once authentication is complete." 
                    )

        # Drain any remaining output after exit
        for key in list(sel.get_map().values()):
            try:
                while True:
                    data = os.read(key.fileobj.fileno(), 4096)
                    if not data:
                        break
                    output.extend(data)
                    log(f"[{key.data}] {data.decode(errors='replace')}")
            except OSError:
                pass

        sel.close()

        full_output = output.decode(errors="replace")

        log(f"Process exited with code {rc}")

        if rc == 0 or full_output.lower().find("already logged in") >= 0:
            log("✓ Successfully authenticated!")
            return True

        log("❌ Authentication failed")
        log(full_output)
        send_notification(
            "Loophole Tunnel - Authentication Failed", 
            f"{full_output}\n\n"
            "Please check the logs for details."
        )
        return False
            
    except Exception as e:
        log(f"❌ Login check error: {e}")
        import traceback
        traceback.print_exc()
        return False


def start_tunnel(options):
    """Start the loophole tunnel"""
    global tunnel_process
    
    if not is_valid(options):
        log("ERROR: Configuration invalid - hostname and port must be set")
        return False
    
    with lock:
        if tunnel_process is not None and tunnel_process.poll() is None:
            log("Tunnel already running")
            return True
        
        try:
            port = int(options["port"])
            hostname = str(options["hostname"]).strip()
            
            command = [
                "loophole",
                "http",
                str(port),
                "homeassistant",
                "--hostname",
                hostname,
            ]
            
            if options.get("verbose", False):
                command.append("--verbose")
            
            log(f"Starting tunnel: {' '.join(command)}")
            
            tunnel_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=get_loophole_env(),
            )
            log(f"Tunnel started with PID {tunnel_process.pid}")

            def forward_output():
                for line in tunnel_process.stdout:
                    message = line.rstrip()
                    if message:
                        log(f"[tunnel] {message}")

            threading.Thread(target=forward_output, daemon=True).start()

            # A successful Popen only means the process was created; Loophole
            # can still reject the upstream immediately after startup.
            time.sleep(5)
            exit_code = tunnel_process.poll()
            if exit_code is not None:
                tunnel_process = None
                log(f"ERROR: Tunnel exited during startup with code {exit_code}")
                return False

            return True
            
        except Exception as e:
            log(f"ERROR: Failed to start tunnel: {e}")
            send_notification(
                "Loophole Tunnel - Establishment Failed",
                f"The tunnel could not be established: {e}",
            )
            tunnel_process = None
            return False


def stop_tunnel():
    """Stop the loophole tunnel"""
    global tunnel_process
    
    with lock:
        if tunnel_process is None or tunnel_process.poll() is not None:
            log("Tunnel is not running")
            return True
        
        try:
            log(f"Stopping tunnel (PID {tunnel_process.pid})...")
            tunnel_process.terminate()
            tunnel_process.wait(timeout=10)
            log("Tunnel stopped")
        except subprocess.TimeoutExpired:
            log("Tunnel did not stop cleanly, killing...")
            tunnel_process.kill()
        finally:
            tunnel_process = None
        
        return True


def tunnel_watchdog():
    """Monitor tunnel and restart if it crashes"""
    global tunnel_process

    while True:
        time.sleep(5)

        with lock:
            tunnel_exited = tunnel_process is not None and tunnel_process.poll() is not None
            if tunnel_exited:
                exit_code = tunnel_process.returncode
                log(f"WARNING: Tunnel process exited with code {exit_code}")
                tunnel_process = None

        if tunnel_exited:
            send_notification(
                "Loophole Tunnel - Connection Lost",
                f"The tunnel process exited unexpectedly with code {exit_code}.",
            )
            options = load_options()
            if is_valid(options):
                log("Attempting to restart tunnel...")
                start_tunnel(options)


def check_tunnel_connectivity(options):
    """Check that the public tunnel URL responds to an HTTP GET."""
    hostname = str(options.get("hostname", "")).strip()
    url = f"https://{hostname}.loophole.site"
    request = urllib.request.Request(url, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if 200 <= response.status < 400:
                log(f"Connectivity check succeeded: {url} returned HTTP {response.status}")
                return True
            log(f"Connectivity check failed: {url} returned HTTP {response.status}")
    except Exception as e:
        log(f"Connectivity check failed for {url}: {e}")

    return False


def main():
    """Main entry point"""
    try:
        log("========================================")
        log("Loophole Tunnel App starting")
        log("========================================")
        
        # Create data directory
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        # Load options
        options = load_options()
        log(
            f"Configuration: port={options.get('port')}, hostname={options.get('hostname')}, "
            f"verbose={options.get('verbose', False)}, "
            f"connectivity_check_interval={options.get('connectivity_check_interval', 15)} minutes"
        )
        
        # Check if logout on restart is requested
        if options.get('logout_on_restart', False):
            log("Logout on restart is enabled - performing logout...")
            run_logout()
            # Reset the option back to false via API
            update_option_via_api("logout_on_restart", False)
            log("Logout on restart has been disabled")
        
        # Check authentication status on every startup
        log("Checking Loophole authentication...")
        authenticated = run_login_check()
        
        if not authenticated:
            log("Not authenticated with Loophole - tunnel will not start until authenticated")
            return
        
        # Start watchdog thread
        watchdog = threading.Thread(target=tunnel_watchdog, daemon=True)
        watchdog.start()
        log("Watchdog thread started")
        
        # Start tunnel only if configuration is valid AND authenticated
        if is_valid(options):
            if authenticated:
                log("Configuration valid and authenticated, starting tunnel...")
                if not start_tunnel(options):
                    log("Tunnel failed to start; check the tunnel log for details")
                    return
            else:
                log("Configuration valid but not authenticated - tunnel not started")
                return
        else:
            log("Configuration is not valid yet. Please configure port and hostname.")
            return
        
        log("App ready")

        try:
            check_interval = max(1, int(options.get("connectivity_check_interval", 15))) * 60
        except (TypeError, ValueError):
            check_interval = 15 * 60
        next_connectivity_check = time.monotonic() + check_interval
        
        # Keep running
        while True:
            time.sleep(60)
            if time.monotonic() >= next_connectivity_check:
                if not check_tunnel_connectivity(options):
                    log("Public tunnel is unreachable; restarting tunnel...")
                    stop_tunnel()
                    start_tunnel(options)
                next_connectivity_check = time.monotonic() + check_interval
            
    except KeyboardInterrupt:
        log("Received interrupt signal")
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        log("Shutting down...")
        stop_tunnel()
        log("Loophole Tunnel App stopped")


if __name__ == "__main__":
    main()
