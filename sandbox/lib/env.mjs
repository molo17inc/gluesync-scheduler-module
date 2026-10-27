import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const sandboxDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const cfg = {
  name: process.env.SANDBOX_NAME || 'chronos-sandbox',
  portBase: Number(process.env.SANDBOX_PORT_BASE || 5417),
  image: process.env.SANDBOX_IMAGE || 'repo',
  apscheduler: process.env.SANDBOX_APSCHEDULER || '',
  tls: process.env.SANDBOX_TLS !== '0',
};

export const configure = overrides => {
  Object.entries(overrides).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') cfg[k] = v;
  });
};

const slug = s => s.replace(/[^a-zA-Z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase();

export const env = {
  sandboxDir,
  repoDir: path.resolve(sandboxDir, '..'),
  reportsDir: path.join(sandboxDir, 'reports'),

  get name() {
    return cfg.name;
  },
  get portBase() {
    return cfg.portBase;
  },
  get ports() {
    return { corehub: cfg.portBase, chronos: cfg.portBase + 1, admin: cfg.portBase + 2 };
  },
  get tls() {
    return cfg.tls;
  },
  get scheme() {
    return cfg.tls ? 'https' : 'http';
  },
  get imageSpec() {
    return cfg.image;
  },
  get apscheduler() {
    return cfg.apscheduler;
  },
  get runDir() {
    return path.join(sandboxDir, 'run', cfg.name);
  },
  get sharedDir() {
    return path.join(this.runDir, 'shared');
  },
  get stateFile() {
    return path.join(this.runDir, 'state.json');
  },
  get network() {
    return cfg.name;
  },
  get labelArg() {
    return `chronos-sandbox=${cfg.name}`;
  },
  get corehubContainer() {
    return `${cfg.name}-corehub`;
  },
  get mainContainer() {
    return `${cfg.name}-chronos`;
  },

  fakeImage: 'chronos-sandbox/fake-corehub:dev',
  repoBaseImage: 'chronos-sandbox/chronos-repo:dev',
  // Sandbox-only values, not real credentials.
  adminToken: 'sandbox-admin-5c1d',
  licenseText: 'SANDBOX-CHRONOS-LICENSE-3f9a',
  p12Password: 'sandbox',
  users: { admin: 'admin', manager: 'manager', monitor: 'monitor', viewer: 'viewer' },

  chronosImageTag() {
    const base = cfg.image === 'repo' ? 'repo' : slug(cfg.image);
    return `chronos-sandbox/chronos:${base}${cfg.apscheduler ? `-aps${slug(cfg.apscheduler)}` : ''}`;
  },
  caPem() {
    return fs.readFileSync(path.join(this.sharedDir, 'ca.pem'));
  },
};
