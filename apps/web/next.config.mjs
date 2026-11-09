const api = process.env.MIXAI_API_ORIGIN || "http://127.0.0.1:8000";
export default {
  poweredByHeader: false,
  experimental: { proxyClientMaxBodySize: "81mb", proxyTimeout: 240_000 },
  async rewrites() {
    return [{ source: "/mixapi/:path*", destination: api + "/:path*" }];
  }
};
