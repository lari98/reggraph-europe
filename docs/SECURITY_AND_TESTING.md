# Security and testing

Implements the cybersecurity rules from the Pitch Pack. Each row names the threat, the control
in code, and the test that proves it. Tests live in `tests/security/test_hacking.py` unless noted.

## Threat → control → proof

| # | Threat | Control | Test |
|---|---|---|---|
| T01 | Calling the API without logging in | `get_principal` → 401 with `WWW-Authenticate` | `TestAuthentication::test_no_token_is_401` |
| T02 | Garbage / oversized / wrong-scheme tokens | strict parser, size cap, always 401, never 500 | `test_garbage_tokens_are_401_never_500` |
| T03 | Forging or editing a token (role / tenant) | HMAC-SHA256, constant-time compare | `test_tampered_payload_is_rejected` |
| T04 | "alg none" style and unknown key id | fixed algorithm, key looked up by id | `test_alg_none_style_and_unknown_kid` |
| T05 | Using an expired token | expiry with small skew allowance | `test_expired_token`, unit tests |
| T06 | Leaked signing key | key rotation (`kid`), min 32-byte keys, prod refuses placeholders | `test_key_rotation…`, `test_config_cli_fx.py` |
| T07 | Privilege escalation | role matrix, `require(...)` on every route | `TestAuthorization::test_role_matrix` |
| T08 | Admin approving rules / self-approval | separation of duties; four-eyes in store | `test_admin_cannot_approve_rules`, `test_four_eyes_over_http` |
| T09 | Machine feed approving its own candidate | ingestion creates DRAFT only | `test_ingestion_service_cannot_approve…` |
| T10 | Reading another customer's data (IDOR) | tenant from token only; queries always filter tenant | `TestTenantIsolation` |
| T11 | Mass assignment (`tenant_id`, `status` in body) | `extra="forbid"` | `test_tenant_in_body_or_query_is_ignored`, `test_unknown_and_wrong_types_rejected` |
| T12 | SQL injection | placeholders only; bandit/ruff block string SQL | `TestInjection::test_sql_injection_in_path_and_body` |
| T13 | Template / format-string injection | `safe_substitute`, no `str.format` on outside text | `test_template_injection_is_inert` |
| T14 | Path traversal through locale tag | allowlist of tags, length cap | `test_locale_path_traversal` |
| T15 | Trojan-source / invisible / control characters | blocked set, NFC normalisation | `test_trojan_source_and_control_chars_rejected` |
| T16 | Number-parsing denial of service (`1e999999`, 5 000 digits) | strict regex, 15-digit cap | `test_decimal_dos_and_overflow`, `test_ratebook_rejects_bad_rates` |
| T17 | Oversized or streamed body | ASGI byte counter → 413 | `test_oversized_body_is_413`, `test_chunked_oversize_is_413` |
| T18 | Flooding / brute force | token-bucket per client → 429 + `Retry-After`; bounded key table | `TestRateLimit`, unit tests |
| T19 | Information leak in errors | generic localized 500; 422 shows location + type only | `test_unexpected_exception_is_generic_500`, `test_validation_errors_do_not_echo_input` |
| T20 | Clickjacking, sniffing, caching of data | security headers on every response | `test_security_headers_on_success_and_error` |
| T21 | Header injection through request id | allowlist regex, else generated | `test_request_id_header_abuse` |
| T22 | Editing or deleting audit history | hash chain + DB triggers; head anchor | `TestAuditTamper`, `test_audit_chain_detects_edit_and_truncation` |
| T23 | Rewriting an approved rule | immutability triggers; new version instead | `test_approved_rule_content_is_immutable` |
| T24 | Silent overwrite of FX rates | insert-only table + trigger | `test_rates_are_insert_only` |
| T25 | Exposed API docs in production | docs/openapi disabled when `prod` | `test_docs_hidden_in_prod` |
| T26 | Weak production configuration | refuses start: short/placeholder key, in-memory DB | `test_settings_refuse_unsafe_values` |
| T27 | Vulnerable dependencies | `pip-audit` in CI, weekly schedule | `make audit` |

Registry threats (Phase 2) — tests in `tests/security/test_registry_attacks.py` and `tests/unit/test_registry.py`:

| # | Threat | Control | Test |
|---|---|---|---|
| T28 | Reading another customer's entities or groups | tenant from token; every query filters tenant; 404 not 403 | `TestRegistryTenantIsolation::test_other_tenant_sees_nothing` |
| T29 | Linking to or ending another customer's ownership (guessable link ids) | link lookups are tenant-scoped | `test_cannot_link_or_end_across_tenants` |
| T30 | Missing authentication on any registry route | `require(...)` on all ten routes | `TestRegistryAccess::test_every_registry_route_needs_a_token` |
| T31 | Viewer or machine feed writing / reading registry | permission matrix | `TestRegistryAccess::test_role_matrix` |
| T32 | Malformed or hostile identifiers (SQL, traversal, 500-char) | LEI checksum + strict regex, parameterised SQL | `test_hostile_lei_in_path_is_422_or_404_never_500` |
| T33 | Mass assignment / path-body mismatch | `extra="forbid"`, path LEI must equal body LEI | `test_mass_assignment_and_path_body_mismatch` |
| T34 | Percentage tricks (NaN, exponent, 9 × 400 digits, >100) | plain-decimal regex, 2 dp, 0 < p ≤ 100 | `test_percentage_abuse` |
| T35 | Cycle bomb / over-100 % through many small links / very deep chains | cycle check, per-day 100 % check, depth + size caps | `TestGraphAbuse` |
| T36 | Rewriting entity history or ownership links | DB triggers (immutable, no delete, end-once) | `test_database_refuses_edits_to_history` |
| T37 | Hidden registry edits | every change audited in the same transaction | `test_audit_covers_registry_changes_and_detects_tampering` |
| T38 | Anonymous or under-privileged access to exposure / insurance data (viewers and machine feeds excluded) | `require(...)` on all 13 routes; data permissions only for analyst, compliance officer, tenant admin | `test_authentication_required`, `test_roles_without_permission_are_refused` |
| T39 | Reading another tenant's batches, contracts or graph | tenant from token on every query; 404 / empty results | `test_tenants_cannot_see_each_other` |
| T40 | Probing which LEIs exist in other tenants | unknown and foreign LEIs give byte-identical errors that name none | `test_foreign_and_unknown_leis_look_identical` |
| T41 | Mass assignment (tenant, batch id, position id, loader, version) | `extra="forbid"` on every body | `test_mass_assignment_is_rejected` |
| T42 | SQL injection via references, filters, cursors, currency | strict reference regex, parameterised SQL, enum patterns | `test_sql_injection_is_inert` |
| T43 | Numeric abuse: floats, exponent, NaN, huge, negative, wrong types | `parse_money_amount` before model parsing | `test_malformed_amounts` (18 cases × 2 endpoints) |
| T44 | Oversized batches / unbounded queries | ≤ 1000 positions, 256 KiB body, 50 000 positions per query (refuse, never truncate) | `test_size_limits`, `test_query_size_guard` |
| T45 | Rewriting exposure or contract history | DB triggers: no update, no delete | `test_database_refuses_tampering` |
| T46 | Half-loaded batch after a failure | one transaction; nothing stored on error | `test_failed_load_leaves_no_partial_data` |
| T47 | Server crash (500) from huge path integers — **found by this suite**, also fixed in the Phase 2 end-link route | path integers range-checked (1..2·10⁹) | `test_huge_ids_never_crash_the_server` |
| T48 | Altering a stored stress result, its signature, inputs or scenario | verify recomputes from the stored snapshot and checks all hashes and the signature | `test_tampering_is_detected` |
| T49 | Moving a valid signature onto another run | signature covers tenant, run id, scenario@version and both hashes | `test_a_signature_cannot_be_moved_to_another_run` |
| T50 | Replaying an access-token signature as a result signature (or the reverse) | domain-separated MAC prefix | `test_signer_roundtrip_rotation_and_forgery` |
| T51 | Reading, running or verifying another tenant's scenarios and runs | tenant from token; 404; foreign and unknown LEIs give identical errors | `test_tenants_are_isolated`, `test_foreign_scenario_leis_look_like_unknown_ones` |
| T52 | Percentage / buffer abuse (exponent, NaN, float, negative, out of range, 400 digits, floods) | plain-decimal regexes, range checks, size caps | `test_percent_abuse`, `test_buffer_abuse`, `test_scenario_size_limits` |
| T53 | Rewriting scenario or run history | DB triggers (both back ends) | `test_history_cannot_be_rewritten` |
| T54 | Database credentials leaking through logs or `repr` | `database_url` excluded from `repr`; documented as a secret | `test_settings_never_print_the_database_url` |
| T55 | SQL injection / `%` and `?` in data on PostgreSQL | driver-side binding; `%` escaped before placeholder translation | `test_parameters_are_data_not_sql` |
| T56 | Two processes handing out the same id on PostgreSQL | advisory lock per transaction | `test_concurrent_writers_do_not_collide` |
| T57 | SSRF through connectors (private/loopback/metadata addresses, look-alike hosts, userinfo, odd ports, header splitting, caller-chosen URLs) | allow-listed hosts from settings, all resolved addresses must be public, URL character rules | `test_bad_urls_are_refused`, `test_non_public_addresses_are_refused`, `test_callers_cannot_steer_the_outbound_request` |
| T58 | DNS rebinding between check and connect | connection pinned to the checked address | `test_good_request_is_pinned_to_the_checked_address` |
| T59 | Man-in-the-middle / wrong certificate name | verified TLS ≥ 1.2 for the real host name | `test_real_transport_checks_the_certificate_name`, `test_real_transport_rejects_an_untrusted_certificate` |
| T60 | Oversized, slow, redirecting or wrong-type responses | size/time caps, redirects refused, content-type allow-list | `test_real_transport_enforces_the_size_limit`, `test_bad_answers_are_refused`, `test_oversized_and_slow_sources_fail_cleanly` |
| T61 | XML bombs and external entities in feeds | `defusedxml`, DTDs refused, item caps | `test_ecb_xml_attacks_are_refused`, `test_feed_is_capped_and_bad_xml_refused` |
| T62 | Poisoned rate data (exponent, NaN, zero, duplicates, absurd values, partial store) | strict parser + rate-book rules, all-or-nothing | `test_ecb_bad_files_are_refused`, `test_poisoned_rate_file_stores_nothing` |
| T63 | Hostile feed content (links to other sites, markup in titles, control/bidi characters) | EUR-Lex-only https links, text cleaning, JSON + nosniff | `test_feed_links_to_other_places_are_dropped`, `test_hostile_feed_cannot_plant_links_or_markup` |
| T64 | Using the API to hammer a public source / racing runs | one run per connector and tenant per 5 minutes; insert-only rates | `test_hammering_a_connector_reaches_the_source_once`, `test_parallel_runs_store_the_rates_once` |
| T65 | JWT algorithm confusion, `alg: none`, tokens carrying their own keys | RS256/ES256 only, key by `kid` from the key set | `test_hmac_with_the_public_key_as_secret_is_refused`, `test_alg_none_is_refused`, `test_keys_embedded_in_the_token_are_ignored` |
| T66 | Forged, expired, wrong-issuer/audience SSO tokens; bad tenant/role claims | signature, issuer, audience, clock and claim checks; one generic 401 | `test_bad_claims_are_refused`, `test_forged_and_replayed_sso_tokens`, `test_tampered_payload_is_refused` |
| T67 | Key-set flooding with unknown `kid`s | cache + one refresh per minute | `test_keys_are_cached_and_refresh_is_rate_limited` |
| T68 | Tenant or role spoofing via headers, query or a look-alike token | tenant/roles only from verified claims | `test_tenant_comes_only_from_the_verified_claim`, `test_a_sso_token_cannot_pose_as_a_local_one` |
| T69 | Dashboard showing more than the caller's role allows, or other tenants' data | per-section permission, tenant filter on every query | `test_each_role_gets_its_own_view`, `test_machine_accounts_cannot_use_the_dashboard`, `test_other_tenants_see_nothing` |
| T70 | Script injection on the dashboard page | no inline code, CSP `script-src 'self'`, `textContent` only | `test_page_files_are_served_with_a_strict_policy` |
| T71 | Tokens, passwords or DSNs in logs, including tracebacks | record-time masking | `test_no_secrets_reach_the_logs`, `test_secrets_are_masked` |
| T72 | Unsafe configuration (partial SSO, http URLs, IP-literal or wildcard hosts, local tokens off without SSO) | validated at start-up | `test_unsafe_settings_do_not_start` |
| T73 | Rewriting connector history or alert content | DB triggers (both back ends) | `test_history_cannot_be_rewritten` |
| T74 | Network call while travelling (offline mode) | `OfflineFetcher`, connectors refuse to run | `test_connectors_are_switched_off_and_never_touch_the_network` |
| T75 | Demo server reachable from other machines | loopback-only check in `serve`/`demo` | `test_demo_refuses_a_public_address`, `test_serve_refuses_a_public_address_in_offline_mode` |
| T76 | SSO or key-set fetch while offline | start-up validation | `test_offline_mode_rejects_sso_configuration` |
| T77 | Token leaking from the demo link | URL fragment, removed by the page, never stored | `test_page_renders_cleanly` (hash empty) |
| T78 | Markup injected through the link fragment | strict pattern, `textContent` only | `test_no_page_script_can_be_injected` |
| T80 | Page texts written as markup, or storage used for the token | `textContent` only; no storage APIs; checked in source | `test_page_never_writes_texts_as_markup`, `test_app_uses_no_browser_storage` |
| T81 | Hostile parts in the reload fragment (`r=javascript:`, `<b>`, future day, bad theme) | each part matched against a strict pattern, else ignored | `test_fragment_parts_are_validated` |
| T82 | Version endpoint leaking data | returns only version and build fingerprint, `no-store` | `test_version_endpoint_is_public_tiny_and_uncacheable` |
| T83 | Hostile `Accept-Language` | allow-list negotiation, falls back to English | `test_the_notice_follows_the_language_header` |
| T84 | Demo signing key readable by others | key file created owner-only; random key if the disk is read-only | `test_demo_key_is_created_once_and_kept_private` |
| T85 | Stale or wrong build kept after a release | pages notice a new build and reload; changed files served fresh | `test_new_release_reloads_page_and_keeps_the_state`, `test_a_changed_page_file_is_served_without_a_restart` |
| T86 | A map or chart pulling data from outside, or leaking through the page policy | map and charts are self-drawn SVG; no outside request, no inline style | `test_overview_pictures_map_gauge_grid_and_bars`, `test_every_page_renders_cleanly_and_stays_local` |
| T87 | Hostile country code in the address bar rendered or executed | the route accepts only two letters, anything else shows an error; all text is set with `textContent` | `test_country_page_print_view_and_data_page` |
| T88 | Demo data mistaken for real data | every demo name carries "(demo)", rates are labelled invented, demo mode is loopback-only | `test_rich_set_is_large_and_weighted_to_dach` |
| T89 | Demo rule text mistaken for legal advice | rules are labelled "(demo summary)", the page shows a notice, sources link to the official text | `test_rules_page_explains_itself_and_shows_the_pictures`, `test_rules_have_real_references_and_every_status` |
| T90 | Source link used for script injection or leaking the page | links are rendered only for https URLs with `rel="noopener noreferrer"`; rule text is set with `textContent` | `test_rules_page_explains_itself_and_shows_the_pictures` |
| T91 | Rule scope used to enumerate the register without the right | `/v1/rules/{key}/scope` needs `rules:read` and `registry:read`; a service account gets 403, no token 401 | `test_rule_scope_refuses_unknown_keys_missing_rights_and_hostile_input` |
| T92 | Hostile rule key, other tenant, bad date on the scope route | 404/422, no stack trace; tenant isolation gives 404 | `test_rule_scope_refuses_unknown_keys_missing_rights_and_hostile_input` |
| T93 | Graph or timeline labels used to inject markup | labels set with `textContent`/SVG text only; strict CSP unchanged | `test_rule_network_and_drill_down_to_the_evidence` |
| T94 | Drill-down unusable on phone or by keyboard | nodes focusable, Enter opens; no horizontal overflow; German texts | `test_rule_drill_down_is_translated_and_phone_safe` |
| T79 | Unreadable colours / third-party fonts | AA contrast tests, system fonts, CSP `img-src 'self'` | `test_text_colour_pairs_meet_wcag_aa`, `test_fonts_and_shapes` |

Property-based tests (`TestProperties`, Hypothesis, 200 examples each) check that
`clean_text` never returns dangerous characters, `negotiate` always returns a supported tag,
and `Money` addition / subtraction round-trips; for the registry, any generated LEI validates in
any letter case and `validate_lei` never crashes on arbitrary text.

## Pipeline

| Gate | Command | Fails when |
|---|---|---|
| Style + bug + security patterns | `ruff check` / `ruff format --check` | any rule violation |
| Types | `mypy` (strict) | any type error |
| Tests + coverage | `pytest` | any failure or coverage < 90 % |
| Static security scan | `bandit` | any finding |
| Dependency vulnerabilities | `pip-audit` | known CVE |
| Module versions | `tools/module_manifest.py check` | changed module not bumped |

## Phase 7 additions (not yet done)

External penetration test, fuzzing of the ingestion parsers, load test, container image scan,
backup/restore drill, and a review of this table against the then-current OWASP ASVS.
Run the suite on both back ends before a release (`REGGRAPH_TEST_PG_DSN`). Passing this suite shows the controls above work; it does **not** replace an independent test.

## If something goes wrong

1. Revoke: remove the compromised key id from `REGGRAPH_TOKEN_KEYS` and restart — its tokens stop working.
2. Verify: `GET /v1/audit/verify` per tenant; compare `head` with the last published head.
3. Contain: tenants are isolated by id; disable one tenant's users without touching others.
4. Preserve: the audit chain and rule history are append-only; export before any repair.
