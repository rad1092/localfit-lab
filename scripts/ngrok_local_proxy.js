const http = require('http');
const { URL } = require('url');

const HOST = process.env.NGROK_PROXY_HOST || '127.0.0.1';
const PORT = Number(process.env.NGROK_PROXY_PORT || 8088);
const FRONTEND_TARGET = process.env.NGROK_FRONTEND_TARGET || 'http://127.0.0.1:5173';
const API_TARGET = process.env.NGROK_API_TARGET || 'http://127.0.0.1:8787';

function targetForPath(pathname) {
    return pathname.startsWith('/api/') ? API_TARGET : FRONTEND_TARGET;
}

const server = http.createServer((clientReq, clientRes) => {
    const targetBase = targetForPath(clientReq.url || '/');
    const targetUrl = new URL(clientReq.url || '/', targetBase);
    const proxyReq = http.request(
        targetUrl,
        {
            method: clientReq.method,
            headers: {
                ...clientReq.headers,
                host: targetUrl.host
            }
        },
        (proxyRes) => {
            clientRes.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
            proxyRes.pipe(clientRes);
        }
    );

    proxyReq.on('error', (error) => {
        clientRes.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
        clientRes.end(`proxy error: ${error.message}`);
    });

    clientReq.pipe(proxyReq);
});

server.listen(PORT, HOST, () => {
    console.log(JSON.stringify({
        ok: true,
        url: `http://${HOST}:${PORT}`,
        frontend: FRONTEND_TARGET,
        api: API_TARGET
    }));
});
