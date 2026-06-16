# ug-experiment-calculator

Пакет считает метрики и воронки A/B-экспериментов Ultimate Guitar, сохраняет результаты в ClickHouse и умеет генерировать код графиков Apache ECharts и Confluence Chart macro для рассчитанных метрик.

Основной сценарий: по `exp_id` пакет достает настройки эксперимента, собирает пользователей и подписки, считает накопленные значения по вариациям, сравнивает контрольную ветку `1` с остальными вариациями и записывает результаты в ClickHouse.

## Что умеет проект

- Доставать список экспериментов для доменов UG Monetization, UG Product и UG Growth.
- Читать метаданные эксперимента: даты, платформы, вариации, событие старта, конфигурацию и сегменты.
- Создавать и переиспользовать ClickHouse-таблицу пользователей эксперимента `exp_users_{exp_id}`.
- Поддерживать локальные ClickHouse-кэши подписок `subscriptions` и `subscriptions_transactions`.
- Считать monetization-метрики из `metrics.yaml`.
- Считать накопленные значения метрик по дням и вариациям.
- Считать pairwise-статистику для пар `1 vs N`: `mean_0`, `mean_1`, `mean_diff`, `lift`, `ci_low`, `ci_high`, `pvalue`.
- Считать funnel-агрегаты и pairwise-статистику для воронок из `funnels.yaml`.
- Записывать результаты в таблицы `ug_exp_stats`, `ug_exp_results`, `ug_exp_funnel_stats`, `ug_exp_funnel_results`.
- Генерировать ECharts-код для двух графиков одной метрики: lift по дням и доверительные интервалы по дням.
- Генерировать Confluence Chart macro для графика cumulative p-value одной метрики по дням.

## Установка

Проект рассчитан на Python `>=3.13`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Зависимости описаны в `pyproject.toml`. Внешний ClickHouse-клиент подключается как:

```toml
clickhouse-worker @ git+https://github.com/DarkPatrick/clickhouse-worker.git@main
```

Для реального запуска нужен доступ к ClickHouse и переменные окружения, которые ожидает `clickhouse_worker`.

## Быстрый старт

```python
from ug_experiment_calculator import calculate_exp_info

raw_metrics, cumulative_metric_values, pairwise_metric_results, debug_info = calculate_exp_info(123456)
```

`calculate_exp_info` возвращает:

- `raw_metrics`: словарь `{(client, segment): DataFrame}` с сырыми monetization-агрегатами.
- `cumulative_metric_values`: словарь `{(client, segment): DataFrame}` с накопленными значениями метрик в long-формате.
- `pairwise_metric_results`: словарь `{(client, segment): DataFrame}` с pairwise-статистикой метрик.
- `debug_info`: строка с именами последних временных таблиц.

В обычном прод-сценарии важнее не возвращаемые датафреймы, а ClickHouse-таблицы, куда функция записывает результаты.

## Конфигурация

Конфигурация задается через `ExperimentCalculatorConfig` или переменные окружения с префиксом `EXPERIMENT_`.

```python
from ug_experiment_calculator import ExperimentCalculatorConfig, calculate_exp_info

config = ExperimentCalculatorConfig(
    database="sandbox",
    cluster="ug_core",
    table_prefix="dev_",
    update_subscription_sources=False,
)

calculate_exp_info(123456, config=config)
```

Переменные окружения:

| Переменная | Значение по умолчанию | Описание |
| --- | --- | --- |
| `EXPERIMENT_CH_DATABASE` | `sandbox` | ClickHouse database для создаваемых и читаемых таблиц. |
| `EXPERIMENT_CH_CLUSTER` | `ug_core` | ClickHouse cluster для DDL `ON CLUSTER`. |
| `EXPERIMENT_CH_TABLE_PREFIX` | пусто | Префикс физических таблиц, например `dev_`. |
| `EXPERIMENT_SUBSCRIPTIONS_START_DATE` | `2011-06-01` | Минимальная дата для кэшей подписок. |
| `EXPERIMENT_QUERIES_DIR` | `ug_experiment_calculator/queries` | Директория SQL-шаблонов. |
| `EXPERIMENT_METRICS_YAML_PATH` | `ug_experiment_calculator/metrics.yaml` | Путь к конфигу метрик. |
| `EXPERIMENT_STATS_YAML_PATH` | `ug_experiment_calculator/stats.yaml` | Путь к конфигу summary-статов. |
| `EXPERIMENT_FUNNELS_YAML_PATH` | `ug_experiment_calculator/funnels.yaml` | Путь к конфигу воронок. |
| `EXPERIMENT_DEFAULT_CLIENTS` | `UGT_IOS,UGT_ANDROID,UG_WEB` | Платформы, если в эксперименте не указан список клиентов. |
| `EXPERIMENT_UPDATE_SUBSCRIPTION_SOURCES` | `false` | Обновлять ли кэши `subscriptions` и `subscriptions_transactions` перед расчетом. |

`table_prefix` применяется к физическому имени таблицы. Например, логическая таблица `ug_exp_results` при `table_prefix="dev_"` станет `sandbox.dev_ug_exp_results`.

## Основной пайплайн расчета

`calculate_exp_info(exp_id)` выполняет такой пайплайн:

1. Читает информацию об эксперименте через SQL-шаблон `get_ug_exp_info.sql`.
2. Парсит `clients_list`, `project` и `segments` из конфигурации эксперимента.
3. Если `update_subscription_sources=True`, обновляет таблицы `subscriptions` и `subscriptions_transactions`.
4. Для каждой пары `(client, segment)` создает или обновляет таблицу пользователей `exp_users_{exp_id}`.
5. Создает временную таблицу подписок `exp_subscription_{exp_id}_{session_id}`.
6. Читает monetization-агрегаты через `monetization_metrics.sql`.
7. Читает и считает воронки, разрешенные для текущей платформы.
8. Считает накопленные агрегаты по метрикам и воронкам.
9. Считает pairwise-статистику, где контрольная вариация всегда `1`.
10. Перезаписывает партиции текущего `exp_id/client/segment` в результирующих таблицах.
11. Удаляет временную таблицу подписок.

Если таблица результата еще не существует, она создается по схеме датафрейма. Если существует, пакет удаляет только партиции текущего эксперимента, платформы и сегмента, а затем вставляет свежие строки.

## ClickHouse-таблицы

### Входные и кэш-таблицы

| Таблица | Назначение |
| --- | --- |
| `subscriptions` | Кэш подписок, обновляется блоками по полгода. |
| `subscriptions_transactions` | Кэш транзакций подписок, обновляется блоками по полгода. |
| `exp_users_{exp_id}` | Пользователи эксперимента с `client`, `segment`, `segment_hash`. |
| `exp_subscription_{exp_id}_{session_id}` | Временная таблица подписок для одного запуска расчета. |

`exp_users_{exp_id}` переиспользуется между запусками. Если хэш сегмента изменился, строки этого сегмента удаляются и собираются заново.

### Результирующие таблицы

| Таблица | Что хранит |
| --- | --- |
| `ug_exp_stats` | Накопленные значения метрик по дням, вариациям, платформам и сегментам. |
| `ug_exp_results` | Pairwise-статистика метрик для пар `1 vs N`. |
| `ug_exp_funnel_stats` | Накопленные значения funnel-переходов по дням и вариациям. |
| `ug_exp_funnel_results` | Pairwise-статистика funnel-переходов для пар `1 vs N`. |

`ug_exp_results` содержит ключевые поля:

- `dt`
- `metric`
- `variation_pair`
- `control_variation`
- `test_variation`
- `mean_0`
- `mean_1`
- `mean_diff`
- `lift`
- `ci_low`
- `ci_high`
- `pvalue`
- `exp_id`
- `client`
- `segment`

`ug_exp_stats` хранит summary-статистики из `stats.yaml` в long-формате:

- `dt`
- `variation`
- `metric`
- `value`
- `exp_id`
- `client`
- `segment`

## Метрики

Метрики описаны в `ug_experiment_calculator/metrics.yaml`.

Пример:

```yaml
arpu, $:
  - numerator: revenue
  - denominator: members
  - percentage: false
  - variance: arpu_var
  - platforms: ["UG_WEB", "UG_IOS", "UG_ANDROID", "UGT_ANDROID", "UGT_IOS"]
  - description: "average revenue per user"
```

Поля метрики:

| Поле | Описание |
| --- | --- |
| `numerator` | Колонка числителя в датафрейме monetization-агрегатов. |
| `denominator` | Колонка знаменателя. |
| `percentage` | Если `true`, `mean_0`, `mean_1`, `mean_diff`, `ci_low`, `ci_high` умножаются на `100`. |
| `distribution` | Сейчас используется `bernoulli` для конверсионных метрик без variance-колонки. |
| `variance` | Колонка дисперсии для revenue/count метрик. |
| `platforms` | Список платформ, для которых считать метрику. |
| `description` | Человекочитаемое описание. |

Чтобы добавить новую метрику:

1. Убедиться, что `monetization_metrics.sql` возвращает нужные колонки числителя, знаменателя и, если нужно, дисперсии.
2. Добавить блок в `metrics.yaml`.
3. Включить нужные платформы в `platforms`.
4. Запустить `calculate_exp_info(exp_id)`.

Для `distribution: "bernoulli"` дисперсия считается как `p * (1 - p)`. Для остальных метрик нужна `variance`-колонка.

## Воронки

Воронки описаны в `ug_experiment_calculator/funnels.yaml`.

Текущий конфиг содержит `tour_subscription_funnels` для app-платформ:

```yaml
tour_subscription_funnels:
  - enabled: false
  - query: tour_subscription_funnels
  - name: "Tour subscription funnels"
  - description: "APP Funnels for Tour Install Pro trials, Tour Instant Offer charges, and Tour Post Decline Instant Offer subscriptions"
  - conditions:
      platforms: ["UG_IOS", "UG_ANDROID", "UGT_ANDROID", "UGT_IOS"]
```

Чтобы добавить новую воронку:

1. Добавить SQL-шаблон в `ug_experiment_calculator/queries`.
2. SQL должен возвращать `dt`, `variation`, идентификаторы funnel/transition и колонки `denominator_users`, `numerator_users`.
3. Добавить запись в `funnels.yaml`.
4. Указать платформы через `platforms` или `conditions.platforms`.
5. Явно включить расчет через `enabled: true` или `calculate: true`; по умолчанию воронки не считаются.

Воронки считаются отдельно от обычных метрик. Генерация ECharts для воронок пока не реализована.

## ECharts-графики для метрик

Модуль `ug_experiment_calculator.echarts` генерирует код Apache ECharts для одной метрики одного эксперимента, одной платформы и одного сегмента.

Готовый сценарий:

```python
from ug_experiment_calculator import get_metric_echarts_code

js_code = get_metric_echarts_code(
    exp_id=123456,
    metric="arpu, $",
    client="UG_WEB",
    segment="Total",
    lift_element_id="metric-lift-chart",
    ci_element_id="metric-ci-chart",
)
```

На странице должны существовать контейнеры и подключенный ECharts:

```html
<div id="metric-lift-chart" style="height: 420px"></div>
<div id="metric-ci-chart" style="height: 420px"></div>

<script src="https://cdn.jsdelivr.net/npm/echarts/dist/echarts.min.js"></script>
<script>
  // сюда вставляется js_code
</script>
```

Первый график:

- ось X: дата;
- ось Y: `diff, %`;
- отдельная линия для каждой пары вариаций;
- tooltip показывает дату, значение control-ветки, значение test-ветки, абсолютную разницу и lift.

Второй график:

- ось X: дата;
- ось Y: confidence interval;
- красная пунктирная линия на `0`;
- для каждой пары вариаций две линии доверительного интервала;
- пространство между линиями заполнено полупрозрачным цветом;
- tooltip показывает CI и `p-value`.

На обоих ECharts-графиках подписи дат на оси X повернуты на `30` градусов, а пересекающиеся подписи скрываются через `axisLabel.hideOverlap`.

Если данные уже есть в датафрейме или списке словарей, можно не ходить в ClickHouse:

```python
from ug_experiment_calculator import build_metric_echarts_code, build_metric_echarts_options

options = build_metric_echarts_options(rows)
js_code = build_metric_echarts_code(rows)
```

Ожидаемые колонки `rows`:

- `dt`
- `variation_pair`
- `mean_0`
- `mean_1`
- `mean_diff`
- `lift`
- `ci_low`
- `ci_high`
- `pvalue`

Опционально полезны `control_variation` и `test_variation`: если они есть, данные сортируются по ним.

## Confluence-графики p-value и lift

Модуль `ug_experiment_calculator.confluence_charts` генерирует нативные Confluence Chart macro для одной метрики одного эксперимента, одной платформы и одного сегмента.

```python
from ug_experiment_calculator import get_metric_confluence_chart_code

chart_code = get_metric_confluence_chart_code(
    exp_id=123456,
    metric="arpu, $",
    client="UG_WEB",
    segment="Total",
)
```

По умолчанию возвращается Confluence storage format:

```xml
<ac:structured-macro ac:name="chart">
  ...
</ac:structured-macro>
```

Для wiki markup:

```python
chart_code = get_metric_confluence_chart_code(
    exp_id=123456,
    metric="arpu, $",
    client="UG_WEB",
    segment="Total",
    output_format="wiki",
)
```

График:

- размер `250x125`;
- тип `timeSeries`;
- subtitle внутри Chart macro включен по умолчанию;
- ось X содержит даты в формате `yyyy-MM-dd`;
- ось Y содержит `pvalue` для каждой пары вариаций;
- легенда включена;
- добавляется красная серия `α = 0.05` как уровень значимости.

Для компактного графика `max_x_ticks` по умолчанию равен `2`, поэтому Confluence рисует очень мало подписей дат на оси X. `image_format` по умолчанию `png`, потому что нативный Chart macro поддерживает только `png` и `jpg`. Чтобы поменять subtitle или выбрать JPEG:

```python
chart_code = get_metric_confluence_chart_code(
    exp_id=123456,
    metric="arpu, $",
    client="UG_WEB",
    segment="Total",
    max_x_ticks=3,
    title="p-value",
    image_format="jpg",
)
```

Нативный Chart macro поддерживает настройку цветов серий, но не дает надежного параметра для пунктирной линии, поэтому уровень значимости рисуется красной линией без dash-стиля.

График `diff, %` строится аналогично, но берет колонку `lift` и не добавляет уровень значимости:

```python
from ug_experiment_calculator import get_metric_confluence_lift_chart_code

chart_code = get_metric_confluence_lift_chart_code(
    exp_id=123456,
    metric="arpu, $",
    client="UG_WEB",
    segment="Total",
)
```

Если данные уже есть локально:

```python
from ug_experiment_calculator import (
    build_metric_confluence_chart_code,
    build_metric_confluence_lift_chart_code,
)

pvalue_chart_code = build_metric_confluence_chart_code(rows, "arpu, $")
lift_chart_code = build_metric_confluence_lift_chart_code(rows, "arpu, $")
```

Ожидаемые колонки `rows`: `dt`, `variation_pair`, `pvalue` для p-value chart и `lift` для графика `diff, %`. Опционально полезны `control_variation` и `test_variation`.

## Confluence-таблица эксперимента

Модуль `ug_experiment_calculator.confluence_tables` генерирует Confluence storage table по рассчитанным строкам `ug_exp_results`.

```python
from ug_experiment_calculator import get_experiment_confluence_table_code

table_code = get_experiment_confluence_table_code(
    exp_id=123456,
    thousands_separator=True,
)
```

Если в результате несколько платформ, каждая платформа оборачивается в `ac:structured-macro ac:name="ui-expand"` с названием платформы. Внутри платформы первым идет блок сегмента `Total`, затем остальные сегменты; перед каждым дополнительным сегментом добавляется строка на всю ширину таблицы с названием сегмента.

Строки каждого блока:

- `Variation` - заголовок, дальше метрики;
- `Control` - `mean_0`;
- `Variation N` - `mean_1` для test-вариации;
- `diff, %` - `lift`;
- `pvalue` - `pvalue`;
- `cumulatives` - два Confluence Chart macro друг под другом: cumulative p-value и cumulative diff по датам.

Колонки метрик берутся из `metrics.yaml`: только метрики с `table_position > 0`, порядок по `table_position`. Поле `positive` управляет раскраской p-value:

- `pvalue >= 0.05`: `#fffae6`;
- значимый хороший эффект: `#e3fcef`;
- значимый плохой эффект: `#ffebe6`.

Первый столбец, header row и строки названий сегментов имеют цвет `#eae6ff` и bold-текст.

Форматирование значений:

- значения `Control` и `Variation N` используют `prefix` и `suffix` из `metrics.yaml`;
- `diff, %` всегда выводится с суффиксом `%`;
- `pvalue >= 0.05` округляется до 2 знаков после точки, `pvalue < 0.05` - до 3 знаков;
- значения метрик и `diff, %` не используют экспоненциальную запись: `abs(value) >= 1` округляется до 2 знаков после точки, а `abs(value) < 1` - до первых двух ненулевых цифр, если перед ними не больше 5 нулей; иначе выводится `0.00`.
- по умолчанию целая часть числовых значений разделяется запятой по тысячам: `1234` -> `1,234`, `12345.6789` -> `12,345.6789`; чтобы вернуть прежний вид, передайте `thousands_separator=False`.

Из готовых строк:

```python
from ug_experiment_calculator import build_experiment_confluence_table_code

table_code = build_experiment_confluence_table_code(
    rows,
    metrics_yaml_path="ug_experiment_calculator/metrics.yaml",
    thousands_separator=True,
)
```

Ожидаемые колонки `rows`: `dt`, `metric`, `variation_pair`, `mean_0`, `mean_1`, `lift`, `pvalue`, `client`, `segment`. Опционально полезны `control_variation` и `test_variation`.

Для статистик из `ug_exp_stats` есть аналогичная Confluence-таблица, обернутая в свернутый `ui-expand` с названием `Stats`.

```python
from ug_experiment_calculator import get_experiment_stats_confluence_table_code

stats_table_code = get_experiment_stats_confluence_table_code(
    exp_id=123456,
    thousands_separator=True,
)
```

Строки каждого stats-блока:

- `Variation` - заголовок, дальше статистики из `stats.yaml`;
- `Control` - последнее значение variation `1`;
- `Variation N` - последнее значение variation `N`;
- `cumulatives` - один Confluence Chart macro `250x250` с накопленными значениями статистики по дням, серии сгруппированы по вариациям, ось дат ограничена двумя отсечками.

Колонки статистик берутся из `stats.yaml`: только элементы с `table_position > 0`, порядок по `table_position`. Заголовок использует `display_name`, если он задан. Значения используют те же правила форматирования, `prefix` и `suffix`, что и таблица метрик.

Из готовых строк:

```python
from ug_experiment_calculator import build_experiment_stats_confluence_table_code

stats_table_code = build_experiment_stats_confluence_table_code(
    rows,
    stats_yaml_path="ug_experiment_calculator/stats.yaml",
    thousands_separator=True,
)
```

Ожидаемые колонки `rows`: `dt`, `metric`, `variation`, `value`, `client`, `segment`.

## Confluence-таблица дизайна эксперимента

```python
from ug_experiment_calculator import build_design_confluence_table_code

table_code = build_design_confluence_table_code({
    "UGT_IOS": ios_design_df,
    "UGT_ANDROID": android_design_df,
    "UG_WEB": web_design_df,
})
```

На вход передается словарь `platform -> pandas.DataFrame`. В каждом датафрейме ожидаются колонки:
`Metrics`, `Design / each metric`, `Baseline`, `Lift, %`, `MDE`, `Power`, `Alpha`, `Sample size (per variation)`, `Duration (days)`.

Если имена колонок совпадают полностью, значения берутся по этим именам в указанном порядке. Если в именах есть опечатки или лишние символы, значения берутся в текущем порядке колонок датафрейма, а названия строк все равно выводятся из фиксированного списка выше. Блоки платформ идут горизонтально и разделяются фиолетовой объединенной колонкой.

## Latest summary DataFrame

Модуль `ug_experiment_calculator.summary_tables` возвращает две pandas-таблицы по последней доступной дате эксперимента.

```python
from ug_experiment_calculator import get_latest_experiment_summary_tables

results_df, stats_df = get_latest_experiment_summary_tables(exp_id=123456)
```

`results_df` читается из `ug_exp_results` и содержит строки `client`, `segment`, `variation_pair`, `metric`, `description`, `control`, `test`, `diff`, `diff, %`, `ci_low`, `ci_high`, `pvalue`, `color`.

`stats_df` читается из `ug_exp_stats` и содержит строки `client`, `segment`, `metric`, `description`, `variation`, `value`.

Обе таблицы сортируются по `client`, `segment`, `table_position` и variation-полю, все значения возвращаются строками. `results_df` фильтруется и форматируется через `metrics.yaml`, `stats_df` - через `stats.yaml`; строки с `table_position <= 0`, отсутствующие в конфиге или не разрешенные для платформы через `platforms`, не попадают в результат. В колонке `metric` выводится `display_name` из конфига, если он задан. Поле `color` в `results_df` использует ту же p-value раскраску, что Confluence-таблица.

Из готовых строк:

```python
from ug_experiment_calculator import build_latest_experiment_summary_tables

results_df, stats_df = build_latest_experiment_summary_tables(
    results_rows,
    stats_rows,
    metrics_yaml_path="ug_experiment_calculator/metrics.yaml",
    stats_yaml_path="ug_experiment_calculator/stats.yaml",
)
```

## Публичный API

Основные функции верхнего уровня импортируются из `ug_experiment_calculator`.

### Расчет экспериментов

```python
from ug_experiment_calculator import calculate_exp_info
```

- `calculate_exp_info(exp_id, config=None)` - полный расчет одного эксперимента.

### Конфигурация

```python
from ug_experiment_calculator import ExperimentCalculatorConfig
```

- `ExperimentCalculatorConfig.from_env()` - собрать конфиг из переменных окружения.
- `config.full_table(name)` - получить полное имя таблицы с database и table_prefix.
- `config.physical_table(name)` - получить физическое имя таблицы с table_prefix.

### Поиск экспериментов

```python
from ug_experiment_calculator import (
    get_exps_list,
    get_ugm_exps_list,
    get_ugp_exps_list,
    get_ugg_exps_list,
    get_experiment,
)
```

- `get_exps_list(domain)` - список experiment id для указанного домена.
- `get_ugm_exps_list()` - эксперименты UG Monetization.
- `get_ugp_exps_list()` - эксперименты UG Product.
- `get_ugg_exps_list()` - эксперименты UG Growth.
- `get_experiment(id)` - метаданные одного эксперимента.

### Метрики и статистика

```python
from ug_experiment_calculator import (
    calc_cumulative_aggregates,
    calc_metrics_stats_by_variation_pairs,
    calc_stats,
    metric_columns_for_client,
    stats_columns_for_client,
)
```

- `calc_cumulative_aggregates(df)` - накопленные агрегаты по датам и вариациям.
- `calc_metrics_stats_by_variation_pairs(cumulative_df, metrics_yaml_path, control_variation=1, client="")` - pairwise-статистика метрик.
- `calc_stats(...)` - низкоуровневый расчет p-value, Cohen's d и confidence interval.
- `metric_columns_for_client(metrics_yaml_path, client)` - колонки из `metrics.yaml`, нужные платформе.
- `stats_columns_for_client(stats_yaml_path, client)` - колонки из `stats.yaml`, нужные платформе для записи в `ug_exp_stats`.

### Графики

```python
from ug_experiment_calculator import (
    build_design_confluence_table_code,
    build_experiment_confluence_table_code,
    build_experiment_stats_confluence_table_code,
    build_latest_experiment_summary_tables,
    build_metric_confluence_chart_code,
    build_metric_confluence_lift_chart_code,
    build_metric_echarts_code,
    build_metric_echarts_options,
    build_stat_confluence_chart_code,
    get_experiment_confluence_table_code,
    get_experiment_stats_confluence_table_code,
    get_latest_experiment_summary_tables,
    get_metric_confluence_chart_code,
    get_metric_confluence_lift_chart_code,
    get_metric_echarts_code,
    get_stat_confluence_chart_code,
)
```

- `get_metric_echarts_code(...)` - прочитать `ug_exp_results` и вернуть JS для двух ECharts-графиков метрики.
- `build_metric_echarts_code(rows, ...)` - собрать JS для ECharts из готовых строк.
- `build_metric_echarts_options(rows)` - вернуть два ECharts option-объекта.
- `get_metric_confluence_chart_code(...)` - прочитать `ug_exp_results` и вернуть Confluence Chart macro для cumulative p-value.
- `build_metric_confluence_chart_code(rows, metric, ...)` - собрать Confluence Chart macro из готовых строк.
- `get_metric_confluence_lift_chart_code(...)` - прочитать `ug_exp_results` и вернуть Confluence Chart macro для cumulative diff.
- `build_metric_confluence_lift_chart_code(rows, metric, ...)` - собрать lift Confluence Chart macro из готовых строк.
- `get_stat_confluence_chart_code(...)` - прочитать `ug_exp_stats` и вернуть Confluence Chart macro для cumulative-статистики.
- `build_stat_confluence_chart_code(rows, metric, ...)` - собрать Confluence Chart macro по `dt`, `variation`, `value`.
- `get_experiment_confluence_table_code(...)` - прочитать `ug_exp_results` и вернуть Confluence storage table по эксперименту.
- `build_experiment_confluence_table_code(rows, metrics_yaml_path=...)` - собрать Confluence storage table из готовых строк.
- `get_experiment_stats_confluence_table_code(...)` - прочитать `ug_exp_stats` и вернуть Confluence storage table для статистик внутри `ui-expand` `Stats`.
- `build_experiment_stats_confluence_table_code(rows, stats_yaml_path=...)` - собрать Confluence storage table для статистик из готовых строк.
- `build_design_confluence_table_code(platform_frames)` - собрать Confluence storage table по словарю датафреймов дизайна эксперимента.
- `get_latest_experiment_summary_tables(...)` - прочитать latest snapshot из `ug_exp_results` и `ug_exp_stats` и вернуть два отформатированных DataFrame.
- `build_latest_experiment_summary_tables(results_rows, stats_rows, ...)` - собрать latest summary DataFrame из готовых строк.

### Форматирование значений

```python
from ug_experiment_calculator import (
    apply_number_affixes,
    format_diff_percent,
    format_metric_number,
    format_metric_value,
    format_plain_number,
    format_pvalue,
)
```

- `format_metric_number(value)` - округлить число для табличного отображения без экспоненциальной записи.
- `format_metric_value(value, prefix="$", suffix="%")` - добавить префикс/суффикс к отформатированному значению.
- `apply_number_affixes(value, prefix="$", suffix="%")` - добавить префикс/суффикс к готовой числовой строке, сохраняя знак перед префиксом.
- `format_diff_percent(value)` - отформатировать lift/diff и добавить `%`.
- `format_pvalue(value)` - отформатировать p-value по правилам Confluence-таблицы.
- `format_plain_number(value)` - вывести число без экспоненциальной записи и без табличного округления.

### Воронки

```python
from ug_experiment_calculator import (
    calc_cumulative_funnel_aggregates,
    calc_funnel_stats_by_variation_pairs,
    funnel_calculation_enabled,
    load_funnels_config,
    funnel_enabled_for_client,
)
```

- `calc_cumulative_funnel_aggregates(df)` - накопленные denominator/numerator и conversion по funnel-переходам.
- `calc_funnel_stats_by_variation_pairs(cumulative_df, control_variation=1)` - pairwise-статистика воронок.
- `funnel_calculation_enabled(funnel_config)` - проверить, включен ли расчет воронки.
- `load_funnels_config(path)` - загрузить `funnels.yaml`.
- `funnel_enabled_for_client(funnel_config, client)` - проверить, включен ли расчет воронки и разрешена ли она для платформы.

### ClickHouse helpers

```python
from ug_experiment_calculator import (
    clear_exp_temp_tables,
    drop_table,
    drop_exp_partitions,
    update_subscription_source_tables,
)
```

- `update_subscription_source_tables()` - обновить кэши подписок.
- `drop_exp_partitions(...)` - удалить партиции конкретного `exp_id/client/segment` из результирующей таблицы.
- `clear_exp_temp_tables()` - удалить временные таблицы, найденные SQL-шаблоном `get_sloperator_temp_tables.sql`.
- `drop_table(table_name)` - удалить таблицу на кластере.

## SQL-шаблоны

SQL-шаблоны лежат в `ug_experiment_calculator/queries` и читаются через `get_query(query_name, params)`.

Ключевые шаблоны:

| Шаблон | Назначение |
| --- | --- |
| `get_ug_exp_info.sql` | Метаданные эксперимента. |
| `get_ug_exps_ids_to_calc.sql` | Список экспериментов по домену. |
| `exp_raw_data_web.sql` / `exp_raw_data_app.sql` | Схема таблицы пользователей эксперимента. |
| `exp_raw_data_web_insert.sql` / `exp_raw_data_app_insert.sql` | Инкрементальная вставка пользователей по дням. |
| `subscriptions_store_by_sub_date.sql` | Сбор подписок в кэш. |
| `subscription_transactions_store_by_sub_date.sql` | Сбор транзакций в кэш. |
| `subscriptions_joined_by_sub_date.sql` | Временная таблица подписок для эксперимента. |
| `monetization_metrics.sql` | Monetization-агрегаты по вариациям и датам. |
| `tour_subscription_funnels.sql` | Текущая funnel-логика. |
| `create_table_template.sql` | DDL-шаблон для Replicated ClickHouse-таблиц. |

## Важные допущения и ограничения

- Контрольная вариация сейчас жестко считается равной `1` в основном пайплайне.
- Pairwise-результаты строятся только как `1 vs N`, без сравнения test-вариаций между собой.
- ECharts и Confluence-графики реализованы только для обычных метрик; графики для воронок нужно добавлять отдельно.
- `calculate_exp_info` может обновлять большие ClickHouse-кэши подписок. Для локальной разработки часто удобнее ставить `EXPERIMENT_UPDATE_SUBSCRIPTION_SOURCES=false`.
- Таблицы создаются по схеме первого датафрейма. Если добавляется новая колонка, для funnel-таблиц есть `ensure_table_columns`, но для остальных изменений схемы может понадобиться ручная миграция.
- `metrics.yaml` хранит элементы как список одноэлементных словарей; код нормализует их в обычный словарь через `normalize_metric_config`.
- `funnels.yaml` устроен так же и нормализуется через `normalize_funnel_config`.

## Как продолжать разработку

Для другого агента или разработчика самая короткая карта такая:

1. Главный orchestration-файл: `ug_experiment_calculator/calculator.py`.
2. ClickHouse I/O, создание таблиц и SQL-шаблоны: `ug_experiment_calculator/repository.py`.
3. Статистика, накопления, YAML-логика метрик и воронок: `ug_experiment_calculator/metrics.py`.
4. Конфиг окружения и имена таблиц: `ug_experiment_calculator/config.py`.
5. Генерация ECharts: `ug_experiment_calculator/echarts.py`.
6. Генерация Confluence Chart macro: `ug_experiment_calculator/confluence_charts.py`.
7. Генерация Confluence summary tables: `ug_experiment_calculator/confluence_tables.py`.
8. Latest summary DataFrame: `ug_experiment_calculator/summary_tables.py`.
9. Общие цвета таблиц: `ug_experiment_calculator/colors.py`.
10. Форматирование значений для таблиц: `ug_experiment_calculator/value_formatting.py`.
11. Публичные импорты: `ug_experiment_calculator/__init__.py`.
12. Метрики добавляются в `ug_experiment_calculator/metrics.yaml`.
13. Summary-статы добавляются в `ug_experiment_calculator/stats.yaml`.
14. Воронки добавляются в `ug_experiment_calculator/funnels.yaml` и `ug_experiment_calculator/queries`.

Перед изменениями в расчетах полезно прогнать:

```bash
python -m compileall ug_experiment_calculator
```

Если доступен ClickHouse и `clickhouse_worker`, дополнительно стоит запустить расчет на небольшом эксперименте или собрать ECharts-код для уже рассчитанного `exp_id`.
