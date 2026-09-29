ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY pam/sentinel-* /etc/pam.d/
COPY pam/pam-*.conf /etc/sentinel/
COPY services/sentinel-runtime-mounts.service /usr/lib/systemd/system/
COPY services/sentinel-apparmor.service /usr/lib/systemd/system/
COPY apparmor-load.sh /usr/local/libexec/sentinel-apparmor-load
COPY install-apt.sh /usr/local/libexec/sentinel-image-install
COPY install-account.sh /usr/local/libexec/sentinel-account-install
RUN /bin/sh /usr/local/libexec/sentinel-image-install debian 13 && /bin/sh /usr/local/libexec/sentinel-account-install && rm /usr/local/libexec/sentinel-image-install /usr/local/libexec/sentinel-account-install
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/sbin/init"]
CMD []
