/** @type {import('next').NextConfig} */
const nextConfig = {
  // Disabled StrictMode - causes double mount/unmount which breaks LiveKit connections
  reactStrictMode: false,

  // Output standalone build for Docker
  output: 'standalone',
}

module.exports = nextConfig
