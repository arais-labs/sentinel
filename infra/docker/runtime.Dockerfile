FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Ubuntu 24.04's chromium-browser is a snap stub (broken in Docker).
# Pull real Chromium from Debian bookworm, pinned so nothing else leaks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gpg \
    && curl -fsSL https://ftp-master.debian.org/keys/archive-key-12.asc \
       | gpg --dearmor -o /etc/apt/keyrings/debian-bookworm.gpg \
    && echo 'deb [signed-by=/etc/apt/keyrings/debian-bookworm.gpg] http://deb.debian.org/debian bookworm main' \
       > /etc/apt/sources.list.d/debian-bookworm.list \
    && printf 'Package: *\nPin: release o=Debian,n=bookworm\nPin-Priority: 100\n' \
       > /etc/apt/preferences.d/chromium-from-debian \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        # Desktop (KDE Plasma + KWin X11 window manager for decorations)
        kde-plasma-desktop \
        kwin-x11 \
        plasma-nm \
        konsole \
        dolphin \
        dbus-x11 \
        at-spi2-core \
        novnc \
        websockify \
        x11vnc \
        xvfb \
        # SSH
        openssh-server \
        # Browser (from Debian bookworm — real .deb, not snap)
        chromium \
        # Dev tools
        socat \
        build-essential \
        git \
        htop \
        jq \
        net-tools \
        procps \
        python3 \
        python3-pip \
        python3-venv \
        ripgrep \
        sudo \
        tree \
        wget \
    && rm -rf /var/lib/apt/lists/*

# Create sentinel user with sudo (no password)
RUN useradd -m -s /bin/bash sentinel \
    && passwd -d sentinel \
    && echo "sentinel ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# SSH setup
RUN mkdir -p /var/run/sshd \
    && sed -i 's/#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config \
    && sed -i 's/#PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

# Chromium wrapper — always passes --no-sandbox (required in Docker)
# This replaces the system binary so KDE desktop icons, CLI, and everything else works.
RUN mv /usr/bin/chromium /usr/bin/chromium-real \
    && printf '#!/bin/sh\nexec /usr/bin/chromium-real --no-sandbox "$@"\n' > /usr/bin/chromium \
    && chmod +x /usr/bin/chromium

# Override the system .desktop entry to use our wrapper
RUN mkdir -p /usr/share/applications \
    && sed 's|Exec=chromium|Exec=chromium|g' /usr/share/applications/chromium.desktop \
       > /tmp/chromium-fixed.desktop 2>/dev/null || true \
    && [ -f /tmp/chromium-fixed.desktop ] && mv /tmp/chromium-fixed.desktop /usr/share/applications/chromium.desktop || true

# Workspace
RUN mkdir -p /home/sentinel/workspace \
    && chown -R sentinel:sentinel /home/sentinel

COPY scripts/start-runtime.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 22 6080 9223

CMD ["/start.sh"]
