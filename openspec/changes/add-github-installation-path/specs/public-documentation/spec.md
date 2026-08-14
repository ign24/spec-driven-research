## ADDED Requirements

### Requirement: Bilingual installation and routing parity
The English and Spanish documentation pairs MUST present the same supported installation route, the
same canonical repository coordinate, and the same agent routing conditions. Equivalent nonliteral
translation is accepted; a differing coordinate, route, or routing condition is not.

#### Scenario: Compare the bilingual pair
- **WHEN** parity validation runs over the README pair and documentation homes
- **THEN** both name the same canonical repository coordinate
- **THEN** both present the revision-pinned installation route and the same routing conditions

#### Scenario: One language documents a different route
- **WHEN** one language claims an installation route the other does not
- **THEN** parity validation identifies the file and fails
