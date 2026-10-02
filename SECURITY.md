# Security policy

## Network exposure

Motion Rolling Recorder currently exposes two unauthenticated HTTP endpoints:

- `POST /motion`
- `GET /health`

They are intended only for a trusted local network. Do **not** configure router
port forwarding, reverse-proxy public access, or any other Internet exposure for
port 8099.

An attacker who can reach `/motion` cannot obtain camera credentials through the
endpoint, but can generate false recording incidents and consume storage.

## Camera credentials

Camera credentials are stored by Home Assistant as app options and are used only
to construct the local RTSP connection. Never place real camera usernames,
passwords, Home Assistant secrets, tokens, or LAN configuration dumps in GitHub
issues.

## Reporting a security issue

Until a private security contact is configured for the repository, do not post
credential leaks or exploitable private-network details in a public issue.
Instead, use GitHub's private vulnerability reporting feature if it is enabled
for the repository.
