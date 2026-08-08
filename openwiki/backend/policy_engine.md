---
type: backend_documentation
title: Policy Evaluation Engine
description: Documentation for the Rego policy evaluation engine
---

# Policy Evaluation Engine

The policy evaluation engine uses Rego (Open Policy Agent) to evaluate policies for entities. This document covers thread safety, session management, consistency guarantees, and error handling behavior.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Policy Evaluation Flow](../backend/overview.md#policy-evaluation-flow) - Policy evaluation flow

## Architecture

### Rego Session

A compiled regorus engine over a fixed set of policy sources.

```python
class RegoSession:
    def __init__(self, sources: list[tuple[str, str]]):
        import regorus
        self._engine = regorus.Engine()
        for filename, source in sources:
            self._engine.add_policy(filename, source)
        self.sources = sources
    
    def clone(self):
        """Create a clone for evaluation (must be on same thread)."""
        return self._engine.clone()
    
    @staticmethod
    def eval_rule(engine, rule_path: str):
        """Evaluate a rule; return the parsed JSON value or None when undefined."""
        _UNDEFINED = "<undefined>"
        raw = json.loads(engine.eval_rule_as_json(rule_path))
        if raw == _UNDEFINED:
            return None
        return raw
    
    def evaluate(self, input_doc: dict, rule_path: str, *, gather_prints: bool = False):
        """One evaluation on a fresh clone. Returns (value, prints)."""
        eng = self.clone()
        eng.set_input_json(json.dumps(input_doc))
        if gather_prints:
            eng.set_gather_prints(True)
        value = self.eval_rule(eng, rule_path)
        prints = eng.take_prints() if gather_prints else []
        return value, prints
```

## Thread Safety

### PyO3 regorus engines are UNSENDABLE

**Critical Constraint:** Regorus engines (and their clones) are created using PyO3 (Python-to-Rust bindings) and are **UNSENDABLE** across threads. This means:
- An Engine instance created on Thread A **cannot** be used on Thread B
- Clones inherit this restriction and must be used on their creator thread
- The engine cache must use **thread-local storage** to prevent cross-thread reuse

### Thread-Local Storage Implementation

```python
# Thread-local storage for engine cache
_ENGINE_CACHE_TLS = threading.local()

def _engine_cache() -> dict[str, tuple[str, "RegoSession"]]:
    cache = getattr(_ENGINE_CACHE_TLS, "cache", None)
    if cache is None:
        cache = {}
        _ENGINE_CACHE_TLS.cache = cache
    return cache
```

**Key Design Decisions:**
- Cache is **per-thread** using `threading.local()`
- Each thread gets its own engine instances
- No locking required (thread-local isolation)
- Cache key is UDM type ID with source hash verification

### Thread Safety Checklist

- ✅ Engine instances are created on-demand per thread
- ✅ Clones are created and used within the same thread
- ✅ Cache is stored in `threading.local()` (not global)
- ✅ Source hash verification prevents stale engine reuse
- ✅ No cross-thread engine sharing occurs

## Session Management

### RegoSession Class

```python
class RegoSession:
    """A compiled regorus engine over a fixed set of policy sources.
    
    Compile once, clone() per evaluation — regorus engines are cheap to
    clone but expensive to compile. Clones are thread-local.
    """
    
    def __init__(self, sources: list[tuple[str, str]]):
        import regorus
        self._engine = regorus.Engine()
        for filename, source in sources:
            self._engine.add_policy(filename, source)
        self.sources = sources
    
    def clone(self):
        """Create a clone for evaluation (must be on same thread)."""
        return self._engine.clone()
    
    @staticmethod
    def eval_rule(engine, rule_path: str):
        """Evaluate a rule; return the parsed JSON value or None when undefined."""
        _UNDEFINED = "<undefined>"
        raw = json.loads(engine.eval_rule_as_json(rule_path))
        if raw == _UNDEFINED:
            return None
        return raw
    
    def evaluate(self, input_doc: dict, rule_path: str, *, gather_prints: bool = False):
        """One evaluation on a fresh clone. Returns (value, prints)."""
        eng = self.clone()
        eng.set_input_json(json.dumps(input_doc))
        if gather_prints:
            eng.set_gather_prints(True)
        value = self.eval_rule(eng, rule_path)
        prints = eng.take_prints() if gather_prints else []
        return value, prints
```

### Caching Strategy

**One Entry Per UDM Type Per Thread:**

```python
def _sources_hash(sources: list[tuple[str, str]]) -> str:
    """Hash policy sources to detect changes."""
    h = hashlib.sha256()
    for filename, source in sources:
        h.update(filename.encode())
        h.update(b"\0")
        h.update(source.encode())
        h.update(b"\0")
    return h.hexdigest()

def get_session_for_type(udm_type) -> Optional[RegoSession]:
    """Return cached compiled session for a type, or None when no policies.
    
    Cache key: UDM type ID with source hash verification.
    One entry per type, per thread. Stale versions self-evict.
    """
    sources = _policy_sources_for_type(udm_type)
    if not sources:
        return None
    key = str(udm_type.id)
    digest = _sources_hash(sources)
    cache = _engine_cache()
    cached = cache.get(key)
    if cached and cached[0] == digest:
        return cached[1]
    session = RegoSession(sources)
    cache[key] = (digest, session)
    return session

def clear_engine_cache() -> None:
    """Clear THIS thread's cache. Other threads self-heal via source hash."""
    _engine_cache().clear()
```

**Cache Benefits:**
- Compiled policies are shared within a thread
- Source hash changes trigger recompilation
- Stale versions don't accumulate
- Memory overhead is bounded (types × threads)

## Policy Input Schema

The input schema must match `_input_schema.rego` and `policy_input.py`:

```python
{
  "input_version": 1,
  "action": "view | save | transition | preview | public_type_fields",
  "locale": "en",
  "type_id": "uuid",
  "entity": {...},
  "old_entity": {...},  # Required for save/transition/preview
  "schemas": {...},
  "users": {...},
  "groups": {...},
  "linked_entities": {...},
  "files": {...},
  "user": {...},
  "changed_fields": {...},
  "additional_result": {...},  # Carry-over from view evaluation
  "transition": "string",  # Only for transition action
  "field": "string",       # Only for transition action
  "node_id": "string",     # Only for transition action
  "transition_descriptor": {...},  # Only for transition action
  "candidate_transitions": {...}   # Only for preview action
}
```

## Policy Evaluation Flow

1. **Build Input Document**: Construct the input from entity, user, and context
2. **Validate Input**: Run `validate_policy_input()` against `_input_schema.rego`
3. **Get Cached Session**: Retrieve or create cached RegoSession
4. **Clone Engine**: Create a fresh clone (required due to PyO3 UNSENDABLE constraint)
5. **Evaluate Policy**: Execute `data.udm.result` rule
6. **Process Results**: Parse and normalize messages, grants, and actions
7. **Return Decision**: Return PolicyEvaluationOutput

## Input Schema Consistency

### validate_policy_input Function

Before any policy evaluation, the input document is validated against the Rego schema:

```python
from userdefinedmodel.policy_input import INPUT_VERSION, validate_policy_input

def build_policy_input(...) -> dict:
    # ... build input_doc ...
    
    try:
        validate_policy_input(input_doc)
    except PydanticValidationError as exc:
        logger.error("policy input violates contract: %s", exc)
        raise  # Reraise to fail fast
    return input_doc
```

**Validation Rules:**
1. **`input_version` must match**: Current version is `1` (defined in `policy_input.py`)
2. **Required fields**: `action`, `entity`, `user`, etc. are validated
3. **Type constraints**: UUIDs, strings, booleans, arrays are typed and validated
4. **Nested structures**: `entity`, `user`, `groups` are validated recursively

**Enforcement:**
- `INPUT_VERSION` constant is shared between Python and Rego
- Policy evaluation fails if versions don't match
- Mismatch triggers `PydanticValidationError` which is logged and raised

### Consistency Checks

**Input Version Enforcement:**
```python
# In policy_input.py
INPUT_VERSION = 1

def validate_policy_input(data: dict):
    """Validate input against Rego schema (_input_schema.rego)."""
    # Validate input_version matches
    if data.get("input_version") != INPUT_VERSION:
        raise PydanticValidationError(...)
    # Validate required fields, types, nested structures...
```

**Contract Contract:**
- The `_input_schema.rego` Rego file defines the input contract
- `policy_input.py` provides Python-side validation
- Both must be kept in sync
- `INPUT_VERSION` is the single source of truth for versioning

## Deny-by-Default Behavior

### Default-Deny Logic

```python
def evaluate_policy(...) -> "PolicyEvaluationOutput":
    deny = PolicyEvaluationOutput()  # All fields default to deny
    
    # Step 1: Check UDM type exists
    udm_type = get_udm_type_for_node(node)
    if udm_type is None:
        return deny  # No type = no policies = deny
    
    # Step 2: Check policies exist
    session = get_session_for_type(udm_type)
    if session is None:
        return deny  # No policies attached = deny
    
    # Step 3: Evaluate policy
    try:
        raw_result = session.evaluate(...)
        if not isinstance(raw_result, dict):
            return deny  # Undefined/non-object = deny
        # Parse result...
    except Exception as exc:
        logger.exception("Policy evaluation failed: %s", exc)
        return deny  # Any error = deny
```

### Deny Scenarios

1. **No UDM type**: Entity has no type, cannot evaluate policies
2. **No policy attached**: Type has no policies configured
3. **Undefined result**: Rego returns undefined or non-object
4. **Contract violation**: Input validation fails
5. **Evaluation exception**: Any Python or Rego exception

## Error Handling

### Error Paths

**Validation Errors (PydanticValidationError):**
```python
def build_policy_input(...) -> dict:
    try:
        validate_policy_input(input_doc)
    except PydanticValidationError as exc:
        logger.error("policy input violates contract: %s", exc)
        raise  # Fail fast - contract violation
```
- Input validation errors are logged and re-raised
- They indicate a bug (contract mismatch)
- They should never occur in production

**Evaluation Errors:**
```python
def evaluate_policy(...) -> "PolicyEvaluationOutput":
    try:
        # ... evaluation ...
    except Exception as exc:
        logger.exception("Policy evaluation failed: %s", exc)
        return deny  # Log and return deny
```
- Evaluation errors are caught and logged
- They return a deny output (allow=False, empty grants)
- They never raise exceptions to the caller

### Error Types

1. **PydanticValidationError**: Input schema mismatch → logged and raised
2. **Regorus evaluation errors**: Policy evaluation failures → logged, deny returned
3. **Database errors**: Missing entities/fields → caught, deny returned
4. **Serialization errors**: User/group serialization failures → logged, deny returned
5. **Unknown levels**: Policy messages with invalid levels → logged and dropped

## Complete Flow Diagram

```
Request → API Endpoint
     ↓
[build_policy_input]    # Validate input against schema
     ↓
[validate_policy_input] # Rego schema validation
     ↓
[get_session_for_type]  # Get cached RegoSession or create
     ↓
[RegoSession.clone()]   # Clone engine (thread-local)
     ↓
[engine.evaluate()]      # Run Rego policy
     ↓
[PolicyEvaluationOutput] # Parse results
     ↓
Return decision + messages + grants
```

## Summary

**Key Points:**
1. **PyO3 regorus engines are UNSENDABLE** - must use thread-local storage
2. **Thread-local cache** - one engine per type per thread
3. **Cloning per evaluation** - engines are cloned for each policy check
4. **Deny-by-default** - missing policies, undefined results, or errors → deny
5. **Input validation** - `validate_policy_input()` enforces `_input_schema.rego`
6. **Error handling** - validation errors are raised, evaluation errors return deny
