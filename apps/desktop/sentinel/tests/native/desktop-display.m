#import "DesktopDisplayServer.h"
#include <arpa/inet.h>

static void report(NSDictionary *value) {
    @synchronized (DesktopDisplayServer.class) {
        NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
        fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout); fflush(stdout);
    }
}
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 2 && argc != 3) return 2;
        NSString *root = @(argv[1]);
        DesktopDisplayServer *server;
        @try { server = [[DesktopDisplayServer alloc]
            initWithVideoPath:[root stringByAppendingPathComponent:@"video.sock"]
            controlPath:[root stringByAppendingPathComponent:@"control.sock"]
            input:^BOOL(uint16_t device, const DesktopInputEvent *events, uint16_t count) {
                NSMutableArray *values = [NSMutableArray new];
                for (unsigned i = 0; i < count; i++)
                    [values addObject:@[@(events[i].type), @(events[i].code), @(events[i].value)]];
                report(@{@"device": @(device), @"events": values}); return YES;
            } keyframe:^{ report(@{@"keyframe": @YES}); }
            ownedEndpoint:^(NSDictionary<NSString *, NSString *> *event) {
                if (argc == 3) report(event);
            }];
        } @catch (NSException *error) {
            fprintf(stderr, "%s\n", error.reason.UTF8String);
            return 1;
        }
        CVPixelBufferRef frame;
        if (CVPixelBufferCreate(NULL, 800, 600, kCVPixelFormatType_32BGRA, NULL, &frame)) return 3;
        CVPixelBufferLockBaseAddress(frame, 0);
        memset(CVPixelBufferGetBaseAddress(frame), 127, CVPixelBufferGetDataSize(frame));
        CVPixelBufferUnlockBaseAddress(frame, 0);
        [server setFrame:frame]; CVPixelBufferRelease(frame);
        report(@{@"ready": @YES});
        char *line = NULL; size_t length = 0;
        while (getline(&line, &length, stdin) > 0) {
            NSDictionary *command = [NSJSONSerialization JSONObjectWithData:[@(line) dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil];
            if ([command[@"action"] isEqual:@"close"]) break;
            if ([command[@"action"] isEqual:@"publish"]) {
                uint8_t packet[17] = {[command[@"type"] intValue], [command[@"flags"] intValue]};
                uint32_t size = htonl(1); memcpy(packet + 4, &size, 4); packet[16] = [command[@"value"] intValue];
                [server publish:[NSData dataWithBytes:packet length:sizeof(packet)]];
                report(@{@"published": @YES});
            }
        }
        free(line); [server close];
        // A frame racing shutdown cannot recreate retained display state.
        if (CVPixelBufferCreate(NULL, 800, 600, kCVPixelFormatType_32BGRA, NULL, &frame)) return 3;
        [server setFrame:frame]; CVPixelBufferRelease(frame);
        report(@{@"closed": @YES});
    }
    return 0;
}
