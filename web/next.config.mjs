/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The backend runs on a separate port during dev. Proxy /api/* so the
  // frontend can fetch with relative URLs (no CORS preflight, no env-var
  // juggling). In prod we'd reverse-proxy at the Caddy/Nginx layer.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://127.0.0.1:8765/api/:path*',
      },
    ];
  },
};

export default nextConfig;
