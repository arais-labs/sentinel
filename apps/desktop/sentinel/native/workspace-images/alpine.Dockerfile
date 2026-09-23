ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY pam/sentinel-* /etc/pam.d/
COPY pam/pam-*.conf /etc/sentinel/
COPY services/sentinel-runtime-mounts.openrc /etc/init.d/sentinel-runtime-mounts
COPY install-alpine.sh /usr/local/libexec/sentinel-image-install
COPY install-account.sh /usr/local/libexec/sentinel-account-install
RUN /bin/sh /usr/local/libexec/sentinel-image-install && /bin/sh /usr/local/libexec/sentinel-account-install && rm /usr/local/libexec/sentinel-image-install /usr/local/libexec/sentinel-account-install
STOPSIGNAL SIGTERM
ENTRYPOINT ["/sbin/openrc-init"]
CMD []
