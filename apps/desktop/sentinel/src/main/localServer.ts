import http from 'node:http';
import httpProxy from 'http-proxy';
import sirv from 'sirv';

export interface LocalServerOptions {
  frontendDir: string;
  backendPort: number;
  listenPort: number;
}

export class LocalServer {
  private server?: http.Server;

  get running(): boolean {
    return Boolean(this.server?.listening);
  }

  async start(options: LocalServerOptions): Promise<void> {
    await this.stop();
    const serve = sirv(options.frontendDir, {
      dev: false,
      single: true,
    });
    const proxy = httpProxy.createProxyServer({
      target: `http://127.0.0.1:${options.backendPort}`,
      ws: true,
      changeOrigin: true,
    });
    proxy.on('error', (_error, _req, res) => {
      if ('writeHead' in res) {
        res.writeHead(502, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: 'backend_unavailable' }));
        return;
      }
      res.destroy();
    });

    this.server = http.createServer((req, res) => {
      const url = req.url || '/';
      if (url.startsWith('/api') || url.startsWith('/health') || url.startsWith('/vnc')) {
        proxy.web(req, res);
        return;
      }
      serve(req, res);
    });

    this.server.on('upgrade', (req, socket, head) => {
      const url = req.url || '/';
      if (url.startsWith('/ws') || url.startsWith('/vnc')) {
        proxy.ws(req, socket, head);
        return;
      }
      socket.destroy();
    });

    await new Promise<void>((resolve, reject) => {
      this.server!.once('error', reject);
      this.server!.listen(options.listenPort, '127.0.0.1', () => {
        this.server!.off('error', reject);
        resolve();
      });
    });
  }

  async stop(): Promise<void> {
    if (!this.server) {
      return;
    }
    const server = this.server;
    this.server = undefined;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}
