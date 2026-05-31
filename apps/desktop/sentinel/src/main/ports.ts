import net from 'node:net';

export async function findFreePort(start = 48000): Promise<number> {
  for (let port = start; port < start + 1000; port += 1) {
    if (await isPortFree(port)) {
      return port;
    }
  }
  throw new Error(`No free port found starting at ${start}`);
}

export function isPortFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => {
      server.close(() => resolve(true));
    });
    server.listen(port, '127.0.0.1');
  });
}
