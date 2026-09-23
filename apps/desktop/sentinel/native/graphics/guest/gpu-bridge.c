#define _GNU_SOURCE
#include "gpu_transport.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <linux/vm_sockets.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <unistd.h>

static volatile sig_atomic_t stopped;
static volatile sig_atomic_t stop_writer = -1;

static void stop_handler(int signal_number)
{
    (void)signal_number;
    int saved_errno = errno;
    stopped = 1;
    if (stop_writer >= 0) {
        const unsigned char byte = 1;
        ssize_t ignored = write((int)stop_writer, &byte, sizeof(byte));
        (void)ignored;
    }
    errno = saved_errno;
}

static int make_listener(int domain, const struct sockaddr *address,
                         socklen_t length)
{
    int fd = socket(domain, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0)
        return -1;
    int reuse = 1;
    if ((domain == AF_INET &&
         setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) < 0) ||
        bind(fd, address, length) < 0 || listen(fd, 2) < 0) {
        int saved_errno = errno;
        close(fd);
        errno = saved_errno;
        return -1;
    }
    return fd;
}

static int accept_connection(int listener, int stop_fd,
                             struct sockaddr *address, socklen_t *length)
{
    struct pollfd descriptors[] = {
        {.fd = stop_fd, .events = POLLIN},
        {.fd = listener, .events = POLLIN},
    };
    while (!stopped) {
        if (poll(descriptors, 2, -1) < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (descriptors[0].revents || stopped) {
            errno = ECANCELED;
            return -1;
        }
        if (descriptors[1].revents & POLLIN) {
            int fd = accept4(listener, address, length, SOCK_CLOEXEC);
            if (fd >= 0)
                return fd;
            if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)
                continue;
            return -1;
        }
        if (descriptors[1].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            errno = ECONNABORTED;
            return -1;
        }
    }
    errno = ECANCELED;
    return -1;
}

int main(void)
{
    int result = 1;
    int stop_pipe[2] = {-1, -1};
    int host = -1, proxy = -1, wire = -1;
    int channels[2] = {-1, -1};
    if (pipe2(stop_pipe, O_CLOEXEC | O_NONBLOCK) < 0) {
        perror("GPU bridge stop pipe");
        goto cleanup;
    }
    stop_writer = stop_pipe[1];
    struct sigaction action = {.sa_handler = stop_handler};
    sigemptyset(&action.sa_mask);
    struct sigaction ignore = {.sa_handler = SIG_IGN};
    sigemptyset(&ignore.sa_mask);
    if (sigaction(SIGINT, &action, NULL) < 0 ||
        sigaction(SIGTERM, &action, NULL) < 0 ||
        sigaction(SIGPIPE, &ignore, NULL) < 0) {
        perror("GPU bridge signal handler");
        goto cleanup;
    }
    const struct sockaddr_vm host_address = {
        .svm_family = AF_VSOCK,
        .svm_port = 55668,
        .svm_cid = VMADDR_CID_ANY,
    };
    const struct sockaddr_in proxy_address = {
        .sin_family = AF_INET,
        .sin_port = htons(55667),
        .sin_addr = {.s_addr = htonl(INADDR_LOOPBACK)},
    };
    host = make_listener(AF_VSOCK, (const struct sockaddr *)&host_address,
                         sizeof(host_address));
    if (host < 0) {
        perror("GPU bridge host listener");
        goto cleanup;
    }
    proxy = make_listener(AF_INET, (const struct sockaddr *)&proxy_address,
                          sizeof(proxy_address));
    if (proxy < 0) {
        perror("GPU bridge proxy listener");
        goto cleanup;
    }
    if (puts("{\"event\":\"ready\"}") == EOF || fflush(stdout) == EOF) {
        perror("GPU bridge readiness");
        goto cleanup;
    }
    struct sockaddr_vm peer = {0};
    socklen_t peer_length = sizeof(peer);
    wire = accept_connection(host, stop_pipe[0], (struct sockaddr *)&peer,
                              &peer_length);
    if (wire < 0) {
        if (!stopped)
            perror("GPU bridge host connection");
        goto cleanup;
    }
    if (peer_length != sizeof(peer) || peer.svm_family != AF_VSOCK ||
        peer.svm_cid != VMADDR_CID_HOST) {
        fputs("GPU bridge connection is not from the owning host\n", stderr);
        goto cleanup;
    }
    /* The device proxy connects its command stream before its resource stream. */
    for (unsigned int index = 0; index < 2; index++) {
        channels[index] = accept_connection(proxy, stop_pipe[0], NULL, NULL);
        int no_delay = 1;
        if (channels[index] < 0 ||
            setsockopt(channels[index], IPPROTO_TCP, TCP_NODELAY, &no_delay,
                       sizeof(no_delay)) < 0) {
            if (!stopped)
                perror("GPU bridge proxy connection");
            goto cleanup;
        }
    }
    result = sentinel_gpu_transport_run(wire, channels, stop_pipe[0]);
    if (result != 0 && !stopped) {
        perror("GPU bridge transport");
        result = 1;
    }

cleanup:
    stop_writer = -1;
    if (wire >= 0)
        close(wire);
    for (unsigned int index = 0; index < 2; index++)
        if (channels[index] >= 0)
            close(channels[index]);
    if (host >= 0)
        close(host);
    if (proxy >= 0)
        close(proxy);
    for (unsigned int index = 0; index < 2; index++)
        if (stop_pipe[index] >= 0)
            close(stop_pipe[index]);
    return stopped ? 0 : result;
}
