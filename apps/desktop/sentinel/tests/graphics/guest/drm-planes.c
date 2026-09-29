/* cc drm-planes.c $(pkg-config --cflags --libs libdrm) -o drm-planes */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

int main(void)
{
   int result = 1;
   int fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
   if (fd < 0) {
      perror("open card0");
      return 1;
   }
   drmVersionPtr version = drmGetVersion(fd);
   if (!version || strcmp(version->name, "virtio_gpu")) {
      fputs("Expected Sentinel's virtio_gpu DRM device\n", stderr);
      if (version) drmFreeVersion(version);
      close(fd);
      return 1;
   }
   drmFreeVersion(version);
   if (drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1)) {
      perror("universal planes");
      close(fd);
      return 1;
   }
   drmModePlaneResPtr planes = drmModeGetPlaneResources(fd);
   if (!planes) {
      perror("get planes");
      close(fd);
      return 1;
   }
   unsigned primary = 0, cursor = 0;
   for (uint32_t i = 0; i < planes->count_planes; i++) {
      drmModeObjectPropertiesPtr props = drmModeObjectGetProperties(
         fd, planes->planes[i], DRM_MODE_OBJECT_PLANE);
      if (!props) goto done;
      int found = 0;
      for (uint32_t j = 0; j < props->count_props; j++) {
         drmModePropertyPtr property = drmModeGetProperty(fd, props->props[j]);
         if (!property) {
            drmModeFreeObjectProperties(props);
            goto done;
         }
         if (!strcmp(property->name, "type")) {
            found = 1;
            primary += props->prop_values[j] == DRM_PLANE_TYPE_PRIMARY;
            cursor += props->prop_values[j] == DRM_PLANE_TYPE_CURSOR;
         }
         drmModeFreeProperty(property);
      }
      drmModeFreeObjectProperties(props);
      if (!found) goto done;
   }
   printf("DRM planes: %u primary, %u cursor, %u total\n",
          primary, cursor, planes->count_planes);
   if (!primary || cursor != primary || planes->count_planes != primary + cursor) {
      fputs("Each scanout must expose its supported primary and cursor planes\n", stderr);
      goto done;
   }
   puts("PASS supported primary and cursor planes are advertised");
   result = 0;
done:
   drmModeFreePlaneResources(planes);
   close(fd);
   return result;
}
