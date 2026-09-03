# Release Tooling

Каталог `release/` содержит локальные скрипты подготовки handoff-пакета и пакета для внешнего аудита. Эти скрипты не входят в customer handoff package, но являются каноническим способом его собрать.

## Основной handoff

`release/output/` — это customer handoff package для развёртывания и эксплуатации на оборудовании заказчика. Он intentionally minimal: код приложения, Docker/deploy assets, `.env.example` и только operational docs, нужные для install/deploy/operate.

Рекомендуемая команда перед передачей заказчику:

```powershell
python release/release.py --version X.Y.Z
```

Она выполняет полный pipeline:

1. Проверяет строгую SemVer, полный Git commit и чистое рабочее дерево.
2. Создаёт fresh QA venv и последовательно запускает Python, migration, deploy-security, npm/CSS и release-tooling checks.
3. Собирает временный handoff, при доступном Docker проверяет compose/build/MariaDB smoke.
4. Генерирует `VERSION`, `BUILD_INFO.json`, `RELEASE_VALIDATION.md`, `SHA256SUMS` и только после повторной проверки атомарно публикует пакет.

Customer handoff docs allowlist:

- `README.customer.md` → `README.md`
- `RELEASE_VALIDATION.md`
- `docs/deployment_env.md`
- `docs/wiki/architecture.md`
- `docs/wiki/acceptance.md`
- `docs/wiki/deployment.md`
- `docs/wiki/operations.md`

Developer/source docs such as `README.md`, `docs/wiki/release_validation_checklist.md`,
`docs/wiki/production_deployment_acceptance_checklist.md`, backlog, changelog, decisions,
session logs, audit history, tests, dev configs and release scripts stay out of the
customer handoff package. They remain available through `source/` in the external audit
package.

Полезные варианты:

```powershell
python release/release.py --version X.Y.Z --dry-run
python release/package.py --scratch --name handoff-smoke --force
```

`package.py --scratch` создаёт только `release/scratch/.../UNVALIDATED`. Такой каталог нельзя передавать заказчику или включать в audit package.

## External audit package

`release/for_audit/` — это полный пакет для внешнего аудита: source snapshot, выбранный customer handoff package и исторический audit context. В отличие от `release/output/`, audit package intentionally context-rich.

Пакет для внешнего аудита собирается отдельно:

```powershell
python release/audit_package.py --version X.Y.Z
```

Результат:

```text
release/for_audit/proj_vX.Y.Z/
release/for_audit/proj_vX.Y.Z.zip
```

Состав audit package:

- `source/` — текущий source snapshot без секретов, cache, build/runtime artifacts, customer Excel-файлов и локальных `docs/audit/` artifacts.
- `release_output/` — выбранный handoff package из `release/output/`.
- `audit_history/` — исторические внешние audit inputs из локального `docs/audit/`, отдельно от чистого `source/`.
- `AUDIT_README.md` — краткая инструкция для внешнего аудитора.

По умолчанию `audit_package.py` берёт последний timestamped `release/output/coal-shipments-*`. Перед сборкой он проверяет, что выбранный `release_output` не старше последнего commit и что source tree не содержит uncommitted changes. Если проверка падает, сначала пересоберите handoff:

```powershell
python release/release.py --version X.Y.Z
python release/audit_package.py --version X.Y.Z
```

Если stale `release_output` нужен намеренно, используйте явный bypass:

```powershell
python release/audit_package.py --version X.Y.Z --allow-stale-release-output
```

Дополнительные варианты:

```powershell
python release/audit_package.py --version X.Y.Z --release-dir release/output/coal-shipments-YYYYMMDD-HHMMSS-vX.Y.Z
python release/audit_package.py --version X.Y.Z --no-zip
python release/audit_package.py --version X.Y.Z --force
```

## Generated directories

- `release/output/` — generated customer handoff folders.
- `release/for_audit/` — generated external audit packages.

Эти директории локальные и игнорируются git.

## Target acceptance evidence

Production-like контур формирует `acceptance-report/mariadb-acceptance.json`.
Перед подписанием акта отчёт fail-closed сверяется с неизменяемым package:

```powershell
python release/acceptance.py --package release/output/<package> --report acceptance-report/mariadb-acceptance.json
```

Проверяются version/build/commit, non-root DB user, scoped grants и полный набор
зелёных migration/test/restore/scheduler шагов.
