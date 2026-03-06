---
name: Data Analysis
description: Analyse CSV/JSON data using Python and pandas via codegen
keywords: [data, csv, json, analysis, chart, graph, statistics, pandas, plot, excel, table, aggregate, count, average, sum, データ, 分析, グラフ, 統計, 表, 集計, 平均]
tools: [codegen, shell_exec]
---

# Data Analysis with Python + pandas

Use the codegen tool to write and run Python scripts that analyse data on GitHub Actions.

## Standard analysis script template

```python
import pandas as pd
import json, sys

# --- Load data ---
# CSV
df = pd.read_csv("data.csv")
# JSON (array of objects)
# df = pd.read_json("data.json")
# JSON (nested) — normalise first
# df = pd.json_normalize(json.load(open("data.json")))

# --- Quick overview ---
print("Shape:", df.shape)
print("Columns:", list(df.columns))
print(df.describe())

# --- Analysis ---
result = df.groupby("category")["value"].agg(["count", "mean", "sum"])
print(result.to_markdown())
```

## Generating charts (matplotlib)

```python
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

df = pd.read_csv("data.csv")

fig, ax = plt.subplots(figsize=(10, 6))
df.groupby("category")["value"].sum().plot(kind="bar", ax=ax)
ax.set_title("Values by Category")
ax.set_ylabel("Total Value")
plt.tight_layout()
plt.savefig("chart.png", dpi=150)
print("Chart saved to chart.png")
```

Upload `chart.png` as an artifact, then share in Discord.

## GitHub Actions workflow for analysis

```yaml
name: Data Analysis
on: workflow_dispatch
jobs:
  analyse:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install pandas matplotlib openpyxl
      - run: python analyse.py
      - uses: actions/upload-artifact@v4
        with:
          name: results
          path: |
            *.png
            *.csv
            *.md
```

## Common operations cheat-sheet

| Task | Code |
|---|---|
| Filter rows | `df[df["col"] > 100]` |
| Sort | `df.sort_values("col", ascending=False)` |
| Top N | `df.nlargest(10, "col")` |
| Pivot table | `df.pivot_table(values="val", index="row", columns="col", aggfunc="sum")` |
| Date parsing | `df["date"] = pd.to_datetime(df["date"])` |
| Resample by month | `df.resample("M", on="date")["val"].sum()` |
| Missing values | `df.isnull().sum()` |
| Export CSV | `df.to_csv("output.csv", index=False)` |
| Export Markdown | `print(df.to_markdown(index=False))` |

## Tips
- Always print `df.head()` and `df.dtypes` first to understand the data
- Use `to_markdown()` for Discord-friendly table output
- For large datasets (>100k rows), summarise rather than printing everything
- When users upload files, save them to disk before processing
- Return key findings as concise bullet points, not raw dataframes
