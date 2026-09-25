<p align="center">
  <img src="https://loophole.cloud/img/logo.png" alt="Loophole Logo" width="250"/>
</p>

# HA Loophole App

Reach your Home Assistant form the internet. Secure, fast and easy!

![Supports aarch64 Architecture][aarch64-shield] ![Supports amd64 Architecture][amd64-shield]

[![Open your Home Assistant instance and add this repository.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https://github.com/dasflaigsi/ha-supervisor-apps)

## About

This Home Assistant app exposes your Home Assistant instance through a Loophole tunnel ([https://loophole.cloud/](https://loophole.cloud/)) over HTTPS. The app verifies Loophole authentication at startup, prompts for login when needed, and then starts a persistent tunnel to the local Home Assistant HTTP port.

### What is Loophole
Loophole is a totally free to use HTTPS tunnel, which is hosted in Europe. You can run the service on your local machine which allows you to securely expose services running on that machine to the web, even if they're behind a firewall or NAT. Currently, Loophole serves HTTP/HTTPs requests and plan to introduce TCP connections in future. As a bonus, all traffic from the internet to your local machine will be encrypted with SSL certificates using lets-encrypt. Authentication using Auth0 provides the security to your Loophole tunnels. Loophole lets you have multiple parallel tunnels running with custom host names at any given point along with end-to-end encryption.

## What this app does

At startup, the app:

1. Loads the configured local port and hostname from Home Assistant options.
2. Checks whether the Loophole CLI is authenticated.
3. If not authenticated, starts `loophole account login` and watches the output.
4. Sends a Home Assistant persistent notification with the login URL when authentication is required.
5. Once authentication succeeds, starts the tunnel with `loophole http <port> homeassistant --hostname <hostname>`.
6. Keeps a watchdog thread running to restart the tunnel if it exits unexpectedly.

## Important warning

This app expects an authenticated Loophole account and a valid `hostname` before exposing a public tunnel. If either is missing, the tunnel will not start. Therefore sign up on [https://loophole.cloud/](https://loophole.cloud/)

## Installation

Use above link to direcly open the repository in your Home Assistant 

Or manually:
1. Go to Settings → Apps → Install App
2. Click the ⋮ menu and select "Repositories"
3. Add this repository URL "https://github.com/dasflaigsi/ha-supervisor-apps"
4. Go back to Install App (App-Store)
5. Search for "Loophole Tunnel"

## Configuration

The app expects these options:

- `port`: The local Home Assistant port, usually `8123`.
- `hostname`: The tunnel hostname, for example `myhome`. The resulting tunnel URL will be `https://myhome.loophole.site`.
- `connectivity_check_interval`: Minutes between public tunnel connectivity checks. Defaults to `15`.
- `verbose`: Enables verbose Loophole CLI output in the logs.
- `logout_on_restart`: When enabled, the app will log out the Loophole account on the next restart and then disable this option. This forces a new login on the following restart.

Example configuration:

```yaml
port: 8123
hostname: myhome
connectivity_check_interval: 15
verbose: false
logout_on_restart: false
```

## Authentication workflow

This app does not assume the Loophole CLI is already logged in. On every startup it checks authentication status.

If the CLI is not authenticated, the app starts the login flow and prints an URL to the logs. It also creates a persistent notification in Home Assistant so the login prompt is visible in the UI.

Follow the URL in the notification or in the app log and copy according key, complete the browser login, and the app continues automatically.
(This requires a Loophole account.)

## Start-up behavior

Once the user is authenticated and the config is valid, the app starts a tunnel and your Home Assistant is reachable from the internet through `https://<hostname>.loophole.site`. This can also be used in the Companion App.

The app stores Loophole state in the app data directory so that the login session persists across restarts.

## Logs and troubleshooting

Check the app log in Home Assistant for:

- authentication status
- login URL output
- tunnel startup messages
- restart warnings
- tunnel exit codes

Common issues:

- `hostname` is empty: the app will not start the tunnel.
- `port` is invalid: the app requires a valid TCP port.
- authentication required: complete the browser login flow shown in the log or notification.
- tunnel exits unexpectedly: the watchdog thread will try to restart it automatically.

## Notes

- The Loophole CLI is installed automatically during the app build.
- Authentication state is kept under the app data volume, not in the container filesystem only.
- The app monitors the tunnel process and restarts it if it dies.
- The app uses Home Assistant’s persistent notifications to surface login steps where they are easy to notice.
## License

This app is licensed under the MIT License. See the [repository LICENSE file](../LICENSE) for details.

This app uses the [Loophole CLI](https://github.com/loophole-dev/loophole-client), which is also licensed under the MIT License.