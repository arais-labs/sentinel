#include "GPUConnection.h"
#include "gpu_transport.h"
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

struct sentinel_gpu_connection {
    int wire, channels[2], stop[2];
    int error;
    pthread_t thread;
};

static void *pump(void *argument)
{
    struct sentinel_gpu_connection *connection = argument;
    if (sentinel_gpu_transport_run(connection->wire, connection->channels,
                                   connection->stop[0]) < 0)
        connection->error = errno;
    return NULL;
}

static void release(struct sentinel_gpu_connection *connection)
{
    if (connection->wire >= 0) close(connection->wire);
    for (unsigned i = 0; i < 2; i++) {
        if (connection->channels[i] >= 0) close(connection->channels[i]);
        if (connection->stop[i] >= 0) close(connection->stop[i]);
    }
    free(connection);
}

struct sentinel_gpu_connection *sentinel_gpu_connection_open(const char *path,
                                                             int channels[2])
{
    channels[0] = channels[1] = -1;
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof(address.sun_path)) {
        errno = ENAMETOOLONG;
        return NULL;
    }
    struct sentinel_gpu_connection *connection = calloc(1, sizeof(*connection));
    if (!connection) return NULL;
    connection->wire = connection->channels[0] = connection->channels[1] = -1;
    connection->stop[0] = connection->stop[1] = -1;
    connection->wire = socket(AF_UNIX, SOCK_STREAM, 0);
    if (connection->wire < 0) goto failed;
    strcpy(address.sun_path, path);
    if (connect(connection->wire, (struct sockaddr *)&address, sizeof(address))) goto failed;
    if (pipe(connection->stop)) goto failed;
    for (unsigned i = 0; i < 2; i++) {
        int pair[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair)) goto failed;
        channels[i] = pair[0];
        connection->channels[i] = pair[1];
        int yes = 1;
        if (setsockopt(channels[i], SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes))) goto failed;
        if (fcntl(channels[i], F_SETFD, FD_CLOEXEC) ||
            fcntl(connection->channels[i], F_SETFD, FD_CLOEXEC) ||
            fcntl(connection->stop[i], F_SETFD, FD_CLOEXEC) ||
            fcntl(connection->stop[i], F_SETFL, O_NONBLOCK)) goto failed;
    }
    if (fcntl(connection->wire, F_SETFD, FD_CLOEXEC)) goto failed;
    int error = pthread_create(&connection->thread, NULL, pump, connection);
    if (error) { errno = error; goto failed; }
    return connection;
failed:;
    int saved = errno;
    for (unsigned i = 0; i < 2; i++) {
        if (channels[i] >= 0) close(channels[i]);
        channels[i] = -1;
    }
    release(connection);
    errno = saved;
    return NULL;
}

int sentinel_gpu_connection_stop_fd(const struct sentinel_gpu_connection *connection)
{
    return connection->stop[1];
}

int sentinel_gpu_connection_close(struct sentinel_gpu_connection *connection)
{
    if (!connection) return 0;
    const char byte = 1;
    ssize_t result;
    do { result = write(connection->stop[1], &byte, 1); } while (result < 0 && errno == EINTR);
    /* EAGAIN means a cancellation wake is already queued. */
    pthread_join(connection->thread, NULL);
    int error = connection->error;
    release(connection);
    return error;
}
