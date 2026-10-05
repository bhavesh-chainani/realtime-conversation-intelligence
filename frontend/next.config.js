/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // next dev writes its own AGENTS.md/CLAUDE.md here by default; the repo keeps one CLAUDE.md at the root.
  agentRules: false,
};

module.exports = nextConfig;
