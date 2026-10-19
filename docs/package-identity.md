# Package identity: frontend receipt и маршруты импорта

Статус: **DEV19 v2, проект контракта для независимого re-review**. Этот документ
задаёт интерфейс DEV20; генератор и проверка ниже ещё не реализованы.
Он не разрешает install, build, import, tests, CI или deploy. Фактический
baseline Portal — `b9a876aa22d84dc13435bf970728b033d1345ba8`.
`hatch_build.py::CustomBuildHook.initialize` сейчас проверяет только наличие
`static/index.html` и хотя бы одного `static/assets/*.js`; editable обходит
эту проверку. Наличие этих файлов не устанавливает source freshness.

## Решение и граница утверждения

Поддерживаемый release wheel строится из полного исходного checkout Portal
с подготовленными locked JS dependencies: штатный `npm run build` создаёт
bundle и derived receipt; wheel hook независимо сверяет source inputs и
каждый output byte. Прямой wheel build не запускает npm автоматически и
отказывает при отсутствии подходящей receipt. Editable остаётся отдельным
API development route, без утверждения о release browser assets.

Выбран receipt вместо безусловной сборки из wheel hook: hook работает без
Node/network, а root release и Nix сохраняют собственные build stages.
Одно обязательное «сначала собери frontend» без проверки не различает старый
bundle рядом с изменённым TS. Повторная сборка внутри hook увеличила бы
стоимость и скрыла переход этапа. Receipt — generated artifact, полностью
попадает под существующий ignore `src/qcl_negf_api/static/`; Nix `dist/`
также ignored. Его не коммитят и не редактируют вручную.

Receipt устанавливает соответствие заявленных текущих inputs, recipe и
предъявленных output bytes. Это не подпись builder, не защита от злонамеренного
изготовления согласованных JSON и bytes, не доказательство корректности
транслятора, bitwise reproducibility, browser acceptance или научной приёмки.
Происхождение dependency installation и доверие builder задаёт отдельный
build receipt. Gitlinks остаются authority компонентных ревизий; ещё один
maintained revision lock не вводится.

## Единственный интерфейс генератора

DEV20 добавляет `frontend/tools/asset-receipt.mjs` без новых dependencies.
Из Portal checkout поддерживаются ровно следующие CLI; показанные команды
являются будущим интерфейсом, а не результатом выполненной здесь проверки:

```console
node frontend/tools/asset-receipt.mjs build
node frontend/tools/asset-receipt.mjs verify
```

Скрипт определяет frontend root по собственному `import.meta.url`, независимо
от cwd. Нет опций output/source root, stamp-only, skip-check или environment
bypass. Неизвестные аргументы дают nonzero. Экспортируемые функции для
synthetic fixtures: `buildFrontend(frontendRoot: string, runner?: BuildRunner): Promise<void>`
и `verifyFrontend(frontendRoot: string): Promise<void>`. Тип `BuildRunner`:
`(argv: readonly string[], context: { cwd: string; env: Readonly<Record<string, string>> }) => Promise<{ exitCode: number; stdout: string }>`.
CLI всегда использует default subprocess runner; аргумент injection доступен
только импортирующему test harness, без CLI/environment override.
Он позволяет synthetic runner отвечать на `npm --version`, tsc и Vite без
actual npm/compiler execution; остальной lifecycle, очистка и verification
не подменяются. В `frontend/package.json` только script
`build` меняется на `node tools/asset-receipt.mjs build`; script `check`,
test и Deno command/environment contract остаются прежними.
Generator и hook требуют именно это значение `scripts.build` и отсутствие
`prebuild`/`postbuild`: дополнительные lifecycle commands не входят в v1
recipe. Импорт самого tool module не запускает CLI; CLI выполняется только
при direct invocation.

Build исполняет последовательно local TypeScript и Vite через тот же Node:

```text
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build --mode production
```

cwd обеих команд — frontend root; shell и глобальные `tsc`/`vite` не нужны.
Child environment задаёт `NODE_ENV=production`. Наличие inherited `VITE_*`
или `NODE_OPTIONS`, либо source `.env`/`.env.*`, отвергается до build:
тайные или неучтённые browser inputs не становятся частью этого контракта.
Остальная среда builder не записывается в frontend receipt. Её происхождение
и возможное влияние остаются ограничением воспроизводимости, включая Nix.
Node.js 24 — поддерживаемый reference builder; npm, TypeScript и Vite берутся
из подготовленного окружения. `npm --version` только читает версию; build не
исполняет `npm ci`, install, fetch, Python import или solver.

## Два режима одного формата

| `mode` | Единственный принимаемый generated root | Source для проверки wheel hook |
| --- | --- | --- |
| `source-static` | `../src/qcl_negf_api/static` | Полные реальные bytes `frontend/` в Portal checkout |
| `nix-dist` | `dist` | Те же inputs, но для `vite.config.ts` применяется ровно существующая Nix замена ниже |

Build выбирает ожидаемый режим по `vite.config.ts`: должна присутствовать ровно одна
literal byte sequence `../src/qcl_negf_api/static` в source-static либо
ровно один `outDir: "dist"` и ни одной первой sequence в nix-dist. Остальные
mode representations отвергаются до записи. Это выбор известного root,
а не проверка произвольной effective TypeScript/config semantics по regex:
token в комментарии не доказывает, куда Vite записал результат. До compiler
wrapper самостоятельно очищает выбранный безопасный root целиком; после
build требует вновь созданный payload именно там. Success без такого
payload, в том числе при перенаправлении effective outDir, даёт отказ.
Для nix-dist hook требует в
своём исходном config ровно одну `../src/qcl_negf_api/static` и заменяет её
bytes на `dist` перед хешированием **только этого файла**. Это точная
`substituteInPlace --replace-fail` из `nix/frontend.nix::postPatch`, а не
общая нормализация source/config. Generator хеширует реальные patched bytes;
receipt различает режимы и сохраняет effective recipe. Других transforms нет.

В wheel оба режима проверяют один и тот же физический
`src/qcl_negf_api/static/`: `output_root` ниже описывает место генерации,
а не разрешённый произвольный путь чтения. Nix копирует весь `dist/.` в
frontend output, затем platform копирует весь `${frontend}/.` в static,
включая receipt. Mode не даёт bypass input/output verification.

Nix source projection отличается от общего input inventory ниже:
`nix/frontend.nix` исключает basename `node_modules`/`dist` **на любой
глубине**, затем применяет `lib.cleanSourceFilter`. Совместимость здесь
установлена source-wise для перечисленного текущего frontend tree baseline
`b9a876aa22d84dc13435bf970728b033d1345ba8` с добавлением public generator
`tools/asset-receipt.mjs`, без новых filtered source paths. Она не обещана
для каждого будущего дерева. Hook не воспроизводит Nix filter и не удаляет
из inventory потерянные rows: например, добавленный source
`src/dist/entry.ts` включается в его полный inventory, но отсутствует в Nix
source receipt; такой wheel route обязан fail closed по `inputs`.
То же правило действует для backup/прочих files, исключённых cleanSourceFilter.
До поддержки нового дерева нужно отдельно подтвердить projection compatibility;
в этом ticket filter, root workflow и native Nix acceptance не меняются.

## Точный набор inputs и outputs

Input paths — относительные POSIX paths от frontend root. Рекурсивно
включаются **все** regular files, в том числе hidden files, `tests/`,
`tools/asset-receipt.mjs`, `package.json`, `package-lock.json`, `index.html`,
`tsconfig.json`, `vite.config.ts`, весь `src/` и будущий `public/`.
Только две top-level директории исключаются целиком: `node_modules/` и
`dist/`. Отсутствующие optional directories не добавляются фиктивными rows.
Для build обязательны перечисленные шесть root/build files (включая tool)
и `src/main.tsx`; остальные rows выводятся из фактического дерева.

Symlinks, special files, непредставимые UTF-8 имена и `.env`/`.env.*` в
перечисляемом дереве дают отказ. Имена не нормализуются. Исключённые
dependency/output директории не обходятся. `.git`, backend `src/`, root
`uv.lock`, Nix recipe и private inventory находятся **вне frontend root**:
они не заявлены frontend inputs; их identities принадлежат внешнему route
receipt. Любой новый build input вне frontend, кроме locked dependencies,
требует изменения этого контракта до поддержки такого build.

Output rows — все regular files рекурсивно от selected known generated root,
за исключением ровно `.qcl-negf-frontend-v1.json` в его корне. Иных exclusions
нет: CSS, JS, maps, fonts, images, hidden files также хешируются. Обязательны
непустые `index.html` и хотя бы один непустой `assets/*.js`. Symlinks и special
files отвергаются; лишний файл, включая оставшийся temporary receipt,
не может быть молча проигнорирован. Перенос всего output tree в wheel
сохраняет относительные output paths и bytes.

В каждом inventory row ровно `path`, `bytes`, `sha256` в этом порядке.
`bytes` — точная длина файла, JSON integer 0…9007199254740991 (не boolean).
SHA-256 — 64 lowercase hexadecimal characters от raw bytes. Paths непустые,
относительные, с `/`; запрещены `\`, NUL/control characters, пустые segments,
`.` и `..`. Rows уникальны и отсортированы по unsigned UTF-8 bytes path.
Добавление, удаление и переименование файла меняет inventory.

## Receipt schema v1

Файл — `<output-root>/.qcl-negf-frontend-v1.json`. Объекты имеют ровно поля
таблицы в указанном порядке; unknown/missing fields, duplicate JSON keys,
NaN/Infinity и неверные types отвергаются. JSON canonical encoding:
UTF-8 без BOM, компактные separators `,` и `:`, без ASCII escaping Unicode,
обычное JSON escaping строк, без trailing spaces; файл оканчивается одним
LF. Reader проверяет совпадение bytes с этой canonical формой. Саму receipt
её output inventory не хеширует; её bytes связывает внешний wheel hash.

| Поле | Точное значение / тип |
| --- | --- |
| `format` | `"qcl-negf.frontend-receipt.v1"` |
| `mode` | `"source-static"` либо `"nix-dist"` |
| `recipe` | Объект с полями `id`, `commands`, `output_root`, `node_env`, `output_reset` |
| `recipe_sha256` | SHA-256 canonical JSON bytes объекта `recipe`, без LF |
| `toolchain` | Объект с полями `node`, `npm`, `typescript`, `vite`; точные непустые version strings |
| `inputs` | Полный непустой массив input rows |
| `inputs_sha256` | SHA-256 canonical JSON bytes массива `inputs`, без LF |
| `outputs` | Полный непустой массив output rows |
| `outputs_sha256` | SHA-256 canonical JSON bytes массива `outputs`, без LF |

`recipe.id` равен `"qcl-negf.frontend-build.v1"`; `commands` — ровно
`[["node","node_modules/typescript/bin/tsc","--noEmit"],
["node","node_modules/vite/bin/vite.js","build","--mode","production"]]`.
`output_root` равен `"../src/qcl_negf_api/static"` для source-static и
`"dist"` для nix-dist; `node_env` равен `"production"`.
`output_reset` равен `"clean-selected-root-v1"`: существующий выбранный
generated payload не может быть принят без независимой очистки до compiler.
Изменение recipe вне этих двух вариантов требует нового format/контракта.
Input hashing включает bytes package script и самого generator, поэтому
изменение реализации recipe также делает прежнюю receipt stale.

`toolchain.node` берётся из `process.versions.node` (24.x.y), `npm` — из
единственной version line успешного `npm --version`; обе строки — SemVer
без `v`. TypeScript и Vite версии читаются из local `node_modules` package
metadata и должны совпадать с соответствующими `packages["node_modules/…"]`
в текущем `package-lock.json`. Те же версии сверяются после build. Hook
проверяет structure/version constraints и совпадение TS/Vite с lock без
запуска Node. Это не byte verification dependency directory: integrity
locked installation подтверждает внешний build route, не поле version.
Receipt не содержит timestamps, usernames, абсолютных paths, Git revision
списков, environment dumps, credentials или приватных site values.

## Build lifecycle и fail-closed hook

1. Build определяет mode и проверяет ownership выбранного root по точным
   правилам ниже; до этой проверки не удаляет ничего. Проверяет целиком
   существующий selected output tree без следования symlinks. Затем
   удаляет его прежнюю receipt и **весь selected generated payload**,
   удаляет сам selected root и создаёт его заново пустым до tsc/Vite.
   Если root отсутствовал, создаёт только этот known root. Не выбирает
   путь из receipt, effective outDir или пользовательского аргумента;
   nonselected tree не трогает. Ошибка safety/delete/recreate — отказ до
   compiler и без нового stamp. Параллельные frontend/wheel builds одного
   дерева не поддерживаются; build owner сериализует доступ.
2. Сохраняет input inventory и toolchain до запуска. Исполняет tsc, затем
   Vite; первый failure прекращает процесс с исходным nonzero, без retry.
   Vite сохраняет baseline `emptyOutDir: true`, но freshness больше не
   опирается на его cleanup. Источник assets не дополняется CDN.
3. Снова получает inputs/toolchain и требует полного равенства до/после.
   Снимает полный output inventory только из selected root, ещё раз
   проверяет inputs перед publish. Неполный/пустой output после zero exit,
   вывод только в другом root и изменившийся source дают отказ; старое
   содержимое не восстанавливается и не «усыновляется» новой receipt.
4. Записывает canonical receipt во вновь созданный temporary regular file
   того же output directory с exclusive create, flush и fsync; atomic
   rename публикует финальное имя последним (directory fsync там, где
   поддержан). До rename ready receipt отсутствует. После rename выполняет
   обычную verify; failure удаляет финальную receipt. Cleanup при любой
   ошибке пытается удалить собственный temp/final stamp; невозможность
   удаления отражается дополнительным diagnostic, не заменяя исходную
   ошибку. Не удаляет файлы через unsafe root/symlink. Это
   atomic publication receipt; весь output tree не заявлен atomic snapshot.

Ownership/safety — часть `clean-selected-root-v1`, до любого recursive delete:

- frontend root берётся из real location самого tool. Для source-static
  его basename должен быть `frontend`, а parent — Portal project root:
  `frontend/package.json` имеет `name = "qcl-negf-portal"`, `private = true`
  и exact scripts.build выше; `pyproject.toml` и
  `src/qcl_negf_api/__init__.py` — regular non-symlink layout markers.
  Проверка layout не парсит TOML и не объявляет Python metadata валидной.
  Root для очистки — ровно `<portal-root>/src/qcl_negf_api/static`.
  `src` и `qcl_negf_api` должны существовать как обычные directories.
- Для nix-dist достаточно public frontend project markers: regular
  `package.json` с `name = "qcl-negf-portal"`, `private = true` и exact
  scripts.build выше, плюс обязательные input files. Root для очистки —
  ровно `<frontend-root>/dist`; parent Portal source в frontend-only Nix
  build не предполагается. Sibling `../src/.../static` в этом режиме
  не инспектируется и не удаляется.
- Выбранный root должен быть строгим потомком owning project root, иметь
  именно эти literal relative segments; все segments от owning root до
  target, существующий target и его entries проверяются `lstat`.
  Запрещены symlinks, special files, `.git` entries и filesystem device
  boundaries (каждая entry имеет тот же filesystem device, что owning
  root). Не следовать realpath symlink для превращения чужого дерева в
  «owned». Unsafe tree остаётся нетронутым, compiler runner не вызывается.
- Эти два paths — объявленные exclusively generated areas, без scientific
  данных, checkout, foreign state или вложенных mounts; существующее legacy bundle там
  разрешено удалить без старой receipt. Никаких glob/parent-directory
  deletes, удаления input/node_modules или попыток очистить фактический
  чужой outDir. Cleanup после ошибки удаляет только собственный stamp
  в уже проверенном selected root, без повторного recursive удаления
  нового payload; original failure сохраняется. Это safety contract для
  сериализованного trusted build tree, не защита от конкурентной подмены
  filesystem другим процессом.

Node `verify` только читает inferred output root и применяет те же schema,
inventory и mode правила; оно не пересоздаёт stamp. Wheel hook использует
независимую stdlib Python реализацию
`verify_frontend_assets(portal_root: Path) -> None` в `hatch_build.py`:

- При `target_name != "wheel"` либо `version == "editable"` сохраняет
  существующий return. Editable не читает stamp и не выдаёт release claim.
- Для остальных wheel читает полный Portal frontend source и static;
  применяет только разрешённый mode transform, заново хеширует input и
  output inventories, проверяет их точное равенство receipt и три digest.
  Не вызывает Node, Git, target package import, AiiDA, services или network.
- Missing source/payload/receipt, malformed receipt, неизвестный mode/schema,
  incomplete inventory, stale input/recipe, changed/missing/extra output,
  symlink и I/O error дают `RuntimeError` до упаковки. Не fallback к старому
  existence-only guard, не auto-rebuild, не `skip/pass`.

Сообщение hook имеет вид
`QCL_FRONTEND_RECEIPT_INVALID:<reason>; run npm --prefix frontend run build with prepared locked dependencies`.
`reason` выбирается из `missing`, `schema`, `mode`, `inputs`, `recipe`,
`outputs`, `toolchain`, `unsafe-path`, `io`; diagnostics могут назвать
относительный публичный file path, но не прочитанные contents/private env.
CLI verify возвращает 0 только после всех проверок, при отказе — nonzero
с тем же reason. Наличие старого успешного log не меняет результат.

## Поддерживаемые маршруты и отдельные identity receipts

| Route | Source/dependency selection | Нужное различающее evidence |
| --- | --- | --- |
| Standalone Portal wheel | Полный Portal source checkout, native package metadata, JS lock; frontend build → direct wheel без workspace source overrides | Source commit/dirty state, lock hash, frontend receipt hash, wheel filename/SHA-256 и проверка exact wheel static inventory |
| Standalone Portal editable | Явный абсолютный editable source root; Python dependencies из согласованного wheel set или явно названных editable owners | Actual import paths и distribution metadata; browser assets могут отсутствовать, release asset identity не заявляется |
| Root Python workspace editable | Root gitlinks и `pyproject.toml::tool.uv.sources`, generated root `uv.lock` выбирают Contracts/Results/AiiDA/Portal | Каждый actual import должен принадлежать выбранному component `src`; editable и wheel receipts различаются |
| Root release wheels | `ops/release.ts` вызывает `ops/environment.ts::frontendBuild`: `npm ci --ignore-scripts`, `npm run build`; затем locked sync и `uv build --no-sources`; source graph проверяется повторно | Source-static receipt внутри Portal wheel, четыре wheel hashes и derived release manifest; root workflow здесь не изменяется |
| Nix frontend/application | `nix/frontend.nix` recursive basename exclusions + cleanSourceFilter, native lock + generated npmDepsHash, Node24, точная outDir patch; platform `nix/application.nix` копирует frontend в API build | Projection-compatible source tree, nix-dist receipt и static bytes в wheel/application, exact derivation, output store paths/narHashes, root Git source graph и flake inputs; потерянные source rows дают отказ |
| Julia workspace | Root `julia/Manifest.toml` имеет paths на Core и Runner; Runner `tools/prepare_workspace.jl` делает `Pkg.develop` обеих выбранных component paths | Actual `pathof(QCLNEGF)`/`pathof(QCLNEGFRunner)`, UUID/version, active project/manifest hash и выбранные source commits/dirty bytes |
| Runner standalone | Runner `Project.toml::[sources]` выбирает Core URL + `rev="v0.2.0"`, compat `=0.2.0`; конкретное разрешение устанавливает созданный Pkg manifest | Resolved Core commit/tree из manifest/depot плюс actual pathof; workspace Core post-tag не подменяет этот route |
| Results standalone/workspace | Results требует Contracts `0.2.0`; existing `tests/source_ownership.py` выбирает sibling Contracts, явный абсолютный `QCL_NEGF_TEST_CONTRACTS_SOURCE` или distribution file owner | Expected и actual `state.py`/`artifacts.py` owners совпадают; wheel SHA и source bytes отдельны от location/version guard |

Frontend receipt переносится целиком между build output и wheel только с
совпадающими проверенными inventories; root/Nix build не наследуют native
acceptance друг друга. Наличие прежнего static tree, HTTP 200 или одного
старого passing/missing-asset assertion не доказывает freshness и wheel
acceptance. Sdist сам не даёт release asset claim; wheel из неполного sdist
без полного frontend source/receipt обязан отказаться.

Внешний import/build evidence record для **каждого route/attempt** содержит
`route`, `attempt_id`, `status` (`measured`, `not_measured`, `failed`), exact
command и result/log hash; selected root/component HEAD, dirty diff hash и
gitlink comparison; hashes package metadata/native locks; build artifact
identity либо явное `not_measured`. Это derived evidence, не maintained lock.
Python record также содержит `sys.executable`, `sys.version`, resolved
module `__file__` и его SHA-256 для `qcl_negf_api.api`,
`aiida_qcl_negf.service`, `qcl_negf_results.state`,
`qcl_negf_contracts.artifacts` по применимости; для каждой distribution —
name/version, metadata location, `direct_url.json` при наличии и declared
`RECORD` owner. Отсутствие metadata/direct_url записывается явно, а actual
path сравнивается с независимо выбранным expected owner. Wheel route
дополнительно хранит trusted wheel SHA-256 и сравнение installed bytes с
его RECORD; RECORD без trusted artifact не доказывает происхождение.

Julia record дополнительно содержит Julia version/executable, active
project, manifest SHA, UUID/version/pathof обоих модулей, resolved Pkg
commit/tree/path и source dirty state; sysimage/solver executable hash,
derivation и native test-contract identity либо `not_measured`.
В публичный отчёт не копируют personal absolute roots; private raw receipt
сохраняет точные paths, публичный locator использует named source/artifact
root и относительный path с hash. Expected owners не задаются личными
hardcoded `/home/...` constants.

Одна строка `0.2.0` не разрешает перенос import/test evidence. В прочитанном
source Core tag `v0.2.0` указывает на `92a217a31f2d0cdec503cb8e9c162709d2a89fd8`,
а selected workspace gitlink — post-tag; фактическую ревизию конкретного
execution выводят из его Git/Pkg evidence, а не переписывают этим документом.
Новый root HEAD получает свой evidence scope. Повторное использование
native output требует одновременного совпадения derivation, contents и
native test contract; научная приёмка остаётся самостоятельным gate.

## Приёмка DEV20 и отложенные измерения

Synthetic public directories должны различить healthy receipt/payload,
изменённый TS при старом index/JS, изменённый lock, missing asset, изменённые
output bytes, лишний output, malformed/incomplete receipt и input rename.
Отдельно проверить отсутствие receipt после failing TypeScript/build и
editable bypass без release claim; оба mode требуют positive/negative
inventory controls. Это tiny fixture tests без npm install/реальной сборки.
Не подменять отсутствие Hatchling fake module как доказательство native hook.

Для R1 mandatory control в **каждом** source-static/nix-dist mode:
подготовить в selected root старые valid index/JS/stamp и sentinel в
nonselected/foreign temporary tree. Injected `BuildRunner` отвечает
на version query, возвращает exit0 из tsc и Vite, но не создаёт selected
payload (отдельный вариант пишет только в другой fixture output root,
сохраняя mode token в config). Перед первым compiler call harness проверяет,
что selected root уже пуст и старого stamp нет. Ожидается `outputs` failure,
никакой новой receipt; Python wheel hook отказывает по missing receipt/
payload, sentinel остаётся byte-identical. Positive runner создаёт **новые**
index/JS в selected root на Vite call: receipt публикуется только после
полной verification и содержит новые output hashes. Actual npm/tsc/Vite
не вызываются. Для nix-dist Python hook проверяют в отдельном Portal
fixture с исходным unpatched config после переноса только selected `dist/.`
в его static; nonselected tree не выдают за output этой attempt.
Отдельный unsafe symlink fixture требует
отказа до runner без изменения foreign sentinel. Для C2 synthetic Nix
receipt без `src/dist/entry.ts` при его наличии в hook source должна дать
`inputs` отказ; нельзя исправлять fixture исключением этой source row.

Source-wise compatibility проверяется по сохранённым root frontendBuild,
Nix outDir substitution и копированию `dist/.`/`${frontend}/.`. Native wheel
contents/import/HTTP/light/offline acceptance принадлежит отдельному DEV22
с подготовленными BUILD artifacts. Никакой CDN, remote theme или automatic
dark mode не вводится: весь web остаётся `color-scheme: light` и работает
из локального same-origin bundle. Offline runtime проверяется отдельно от
доступа к registries при cold-source bootstrap.

| Утверждение | Свидетельство | Ограничение | Решение |
| --- | --- | --- | --- |
| Existence-only guard оставляет stale-assets gap | Baseline `hatch_build.py::CustomBuildHook.initialize` | Негативный native wheel здесь не запускался | изменить |
| Receipt задаёт проверяемую связь source/recipe/output | Поля, inventories и fail-closed lifecycle выше | Это проект DEV19, implementation/fixtures ещё нет | изменить |
| Root и Nix имеют отдельные supported build routes | `ops/release.ts`, `ops/environment.ts`, `nix/frontend.nix`, platform `nix/application.nix` | Native derivation/wheel/import receipt не измерены | сохранить |
| Same version не устанавливает imported source | Runner tag/path routes; Results owning-import guard | Actual runtime paths здесь не читались через import | дополнительно измерить |
| Freshness/wheel/browser/scientific acceptance ещё не установлены | Только source и documentation review | Build/runtime/offline/light native checks — `not_measured` | данных недостаточно |
