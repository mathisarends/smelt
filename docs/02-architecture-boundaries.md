# Level 2: Architecture boundaries

Turn "who may import what" from a diagram into enforced rules.

## Layers

```yaml
architecture:
  layers:
    domain: {path: domain, may_depend_on: []}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}

rules:
  architecture/layer-boundary: error   # ARC001
```

`path` matches a package segment, so `shop.application.checkout` is in
`application`. Names are yours; Smelt does not require a DDD layout.

## Fix ARC001 with a port

The application owns an interface; the composition root wires the concrete
class:

```python
# src/shop/application/ports.py
class OrderRepository(Protocol):
    def get(self, order_id: int) -> Order: ...

# src/shop/application/checkout.py
def load_order(orders: OrderRepository, order_id: int) -> Order:
    return orders.get(order_id)

# src/shop/bootstrap.py
print(load_order(SqlOrderRepository(), 1))
```

```yaml
architecture:
  composition_root: [shop.bootstrap]   # may import every layer

rules:
  architecture/composition-root-leak: error   # ARC006: nothing imports the root
```

## Third-party packages

```yaml
architecture:
  layers:
    domain:
      path: domain
      third_party: {default: deny, allow: [pydantic]}

rules:
  architecture/forbidden-third-party-import: error   # ARC003
```

```text
src/shop/domain/pricing.py:1:8 ARC003  domain must not import requests (domain allows only: pydantic)
```

## Features

```yaml
architecture:
  features: {root: app.features}   # every child package is a feature
  shared: [app.shared]             # importable everywhere, imports no feature
  cross_feature:
    default: deny
    allow:
      - "application -> application"
      - {from: orders.application, to: billing.domain}

rules:
  architecture/cross-feature-import: error    # ARC002
  architecture/shared-imports-feature: error  # ARC005
```

Layers apply inside every feature. A forbidden feature import names the
`allow` entry that would permit it, so the decision is explicit.

## Ask before writing

```bash
smelt context src/shop/application/refunds.py --guide
```

```text
Layer: application
Required:
  ARC001  From layer application, import only domain; reach other layers through a port
```

Also useful: `architecture/import-cycle` (ARC004) and
`architecture/unclassified-module` (ARC012) for modules without a role. Details:
[architecture as code](architecture.md).

**Next:** [Level 3: Tests that mirror the source](03-test-layout.md)
