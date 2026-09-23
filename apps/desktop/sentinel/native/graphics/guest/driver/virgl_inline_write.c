#include "virgl_inline_write.h"
#include "virgl_inline_write_packet.h"
#include "virgl_context.h"
#include "virgl_resource.h"
#include "virgl_screen.h"

size_t virgl_inline_buffer_max_bytes(const struct virgl_context *ctx)
{
   if (!ctx || !ctx->cbuf || ctx->cbuf->cdw > VIRGL_MAX_CMDBUF_DWORDS)
      return 0;
   return virgl_inline_packet_max_bytes(VIRGL_MAX_CMDBUF_DWORDS - ctx->cbuf->cdw);
}

bool virgl_inline_buffer_write(struct virgl_context *ctx,
                              struct pipe_resource *resource,
                              uint32_t offset, const void *data, size_t length)
{
   if (!ctx || !ctx->cbuf || !ctx->cbuf->buf ||
       ctx->cbuf->cdw > VIRGL_MAX_CMDBUF_DWORDS || !resource ||
       resource->screen != ctx->base.screen || resource->target != PIPE_BUFFER ||
       offset > resource->width0 || length > resource->width0 - offset ||
       offset > INT_MAX || length > (size_t)INT_MAX - offset ||
       (!data && length) || length > virgl_inline_buffer_max_bytes(ctx))
      return false;
   if (!length)
      return true;
   struct virgl_resource *res = virgl_resource(resource);
   if (!res->hw_res)
      return false;
   unsigned start = ctx->cbuf->cdw;
   size_t dwords = virgl_inline_packet_write(ctx->cbuf->buf + start,
      VIRGL_MAX_CMDBUF_DWORDS - start, offset, data, length);
   if (!dwords)
      return false;
   struct virgl_winsys *vws = virgl_screen(ctx->base.screen)->vws;
   ctx->cbuf->cdw = start + 1;
   vws->emit_res(vws, ctx->cbuf, res->hw_res, true);
   ctx->cbuf->cdw = start + dwords;
   util_range_add(resource, &res->valid_buffer_range, offset, offset + length);
   virgl_resource_dirty_tracked_upload(res, 0);
   return true;
}
