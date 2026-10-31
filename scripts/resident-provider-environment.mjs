// Copy only environment references in the selected provider configuration.
export function providerEnvironment(provider, environment) {
  const names = [...JSON.stringify(provider).matchAll(/\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))/g)]
    .map(match => match[1] ?? match[2]);
  if (names.some(name => /GITHUB|GH_TOKEN/.test(name)))
    throw new Error('Repository credentials cannot enter an eval');
  return Object.fromEntries([...new Set(names)].filter(name => environment[name] !== undefined)
    .map(name => [name, environment[name]]));
}
