import aiAgent from './ai-agent.mjs';
import chains from './chains.mjs';
import coreHubFaults from './corehub-faults.mjs';
import coreHubOutage from './corehub-outage.mjs';
import coreHubSlow from './corehub-slow.mjs';
import coreHubTasks from './core-hub-tasks.mjs';
import dstFallbackCustomer from './dst-fallback-customer.mjs';
import dstFallbackFreeze from './dst-fallback-freeze.mjs';
import dstFallbackPatterns from './dst-fallback-patterns.mjs';
import dstSpringforward from './dst-springforward.mjs';
import internalApi from './internal-api.mjs';
import jobsCrud from './jobs-crud.mjs';
import maintenance from './maintenance.mjs';
import queryStudio from './query-studio.mjs';
import restartCatchup from './restart-catchup.mjs';
import settings from './settings.mjs';
import taskTypes from './task-types.mjs';
import triggers from './triggers.mjs';
import upgradeFromGa from './upgrade-from-ga.mjs';
import webhooks from './webhooks.mjs';

// Function coverage first (fast, main instance), then resilience, then the DST and
// restart scenarios (slow, one container per scenario or cell).
export const scenarios = [
  jobsCrud,
  taskTypes,
  chains,
  triggers,
  webhooks,
  internalApi,
  settings,
  queryStudio,
  aiAgent,
  coreHubTasks,
  maintenance,
  coreHubFaults,
  coreHubOutage,
  coreHubSlow,
  dstFallbackFreeze,
  dstFallbackCustomer,
  dstFallbackPatterns,
  dstSpringforward,
  restartCatchup,
  upgradeFromGa,
];
