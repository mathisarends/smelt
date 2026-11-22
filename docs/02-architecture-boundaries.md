# Level 2: Architecture boundaries

Level 1 ended with an ARC001 finding: the application layer imported the SQL
repository. This guide describes the shop's architecture in `smelt.yml`, fixes
that finding the way the rule intends and then grows the policy to features,
shared code and third-party packages.

## Layers

A layer is a named part of your code with a list of layers it may import:

```yaml
architecture:
  layers:
    domain: {path: domain, may_depend_on: []}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}

rules:
  architecture/layer-boundary: error   # ARC001
```

`path` matches a package segment: `shop.application.checkout` belongs to
`application`. Dependencies point in one direction, so the domain can be tested
without a database and the application without a web framework.

> [!NOTE]
> Layer, folder and feature names come from your policy. Smelt does not require
> a DDD layout; a flat library can declare two layers or none at all.

## Fix a boundary finding with a port

The application needs to load orders but must not know about SQL. Give it an
interface it owns:

```python
# src/shop/application/ports.py
from typing import Protocol

from shop.domain.orders import Order


class OrderRepository(Protocol):
    def get(self, order_id: int) -> Order: ...
```

```python
# src/shop/application/checkout.py
from shop.application.ports import OrderRepository
from shop.domain.orders import Order


def load_order(orders: OrderRepository, order_id: int) -> Order:
    return orders.get(order_id)
```

Something still has to create the concrete repository. That is the job of the
**composition root**, the one place allowed to import every layer:

```python
# src/shop/bootstrap.py
from shop.application.checkout import load_order
from shop.infrastructure.database import SqlOrderRepository


def main() -> None:
    print(load_order(SqlOrderRepository(), 1))
```

```yaml
architecture:
  composition_root: [shop.bootstrap]

rules:
  architecture/composition-root-leak: error   # ARC006
```

The composition root may import across layers and features. ARC006 keeps the
dependency one-way: ordinary modules must not import it.

```text
src/shop/application/cli.py:1:28 ARC006 architecture/composition-root-leak
  shop.application.cli must not import the composition root shop.bootstrap
```

When feature packages assemble their own dependencies, declare those modules as
`wiring`, for example `wiring: [app.features.*.infrastructure.di]`. Wiring
modules keep their layer but may cross its boundaries.

## Ask before you write

`smelt context` describes what applies to a module, a feature or a file that
does not exist yet:

```bash
smelt context shop.application
smelt context src/shop/application/refunds.py --guide
```

```text
Guide: shop.application.refunds (src/shop/application/refunds.py) [planned]
Layer: application

Required:
  ARC001  From layer application, import only domain; reach other layers through a port
```

This is the command agents should run before creating a file. Level 4 builds the
agent workflow around it.

## Third-party packages

Each layer can restrict the packages it imports. Keep frameworks and I/O
libraries out of the core:

```yaml
architecture:
  layers:
    domain:
      path: domain
      may_depend_on: []
      third_party: {default: deny, allow: [pydantic]}

rules:
  architecture/forbidden-third-party-import: error   # ARC003
```

```text
src/shop/domain/pricing.py:1:8 ARC003 architecture/forbidden-third-party-import
  domain must not import requests (domain allows only: pydantic)
```

The reverse form, `{default: allow, deny: [sqlalchemy, fastapi]}`, works too.
`smelt init` writes `third_party: allow` for every layer; decide per layer.

## Features

Larger applications split by business capability first and by layer second:

```text
src/app/features/
  billing/{domain,application,infrastructure}/
  orders/{domain,application,infrastructure}/
src/app/shared/
```

```yaml
architecture:
  features: {root: app.features}   # every child package is a feature
  shared: [app.shared]             # importable everywhere, imports no feature
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}
  cross_feature:
    default: deny
    allow:
      - "application -> application"            # every feature, this layer pair
      - {from: orders.application, to: billing.domain}   # one direction, two features

rules:
  architecture/layer-boundary: error          # ARC001
  architecture/cross-feature-import: error    # ARC002
  architecture/shared-imports-feature: error  # ARC005
```

Layers apply inside every feature. Features may import each other only through
`cross_feature.allow`. Without the second entry, `orders` reaching into the
billing domain is a finding, and the hint names the exact entry that would allow
it:

```text
src/app/features/orders/application/place.py:1:49 ARC002 architecture/cross-feature-import
  feature orders must not import feature billing (application -> domain)
  -> If orders may rely on billing, that is a policy decision rather than a bug:
     allow it under architecture.cross_feature.allow as {from: orders.application, to: billing.domain}.
```

`smelt init` infers features, layers, shared modules, the composition root and
wiring from an existing layout. Its `cross_feature.allow` starts empty: feature
relationships are decisions you declare.

## Leave no module unplaced

A module outside every layer, feature and shared set is exempt from boundary
checks. Two rules close that gap:

```yaml
rules:
  architecture/unclassified-module: warning   # ARC012: report modules without a role
  architecture/import-cycle: error            # ARC004: report import cycles
```

Code that spans several packages, such as a central platform package beside the
features, belongs in a layer's `modules` list. See
[architecture as code](architecture.md) for central packages, wiring patterns and
layer assignment by module prefix.

## Next

[Level 3: Tests that mirror the source](03-test-layout.md) puts the test tree
under the same kind of policy.
