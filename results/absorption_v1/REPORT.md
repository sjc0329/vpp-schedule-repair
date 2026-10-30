# 本地与跨Edge吸收实验

六例预先冻结，完成24/24个配置；独立核验可行计划24个。
每例同一事件、发布计划、实际状态与经济参照；原旧实验文件未覆盖。

## 直接吸收充分条件

|案例|本地容量/kWh|总直接吸收容量/kWh|是否足以吸收8kWh|最少直接支援Edge|
|---|---:|---:|---|---:|
|ABS-LOCAL-1|0|0|False|None|
|ABS-LOCAL-2|0|0|False|None|
|ABS-LOCAL-3|8.95718|8.95718|True|0|
|ABS-REMOTE-1|0|0|False|None|
|ABS-REMOTE-2|0|0|False|None|
|ABS-REMOTE-3|0|0|False|None|

## 完整优化

|案例|方法|总修改/kWh|内部修改/kWh|额外成本/元|参与数|证据方案在预算内|
|---|---|---:|---:|---:|---:|---|
|ABS-LOCAL-1|economic|28.4085|68.2503|0|2|False|
|ABS-LOCAL-1|zero_budget|8.05691|36.5821|0|1|False|
|ABS-LOCAL-1|budget_0.64|7.59501|42.4504|0.64|1|False|
|ABS-LOCAL-1|budget_1.92|7.27136|37.6151|1.13891|1|False|
|ABS-LOCAL-2|economic|39.3185|97.02|0|2|False|
|ABS-LOCAL-2|zero_budget|8|8|-2.84217e-14|0|False|
|ABS-LOCAL-2|budget_0.64|7.88661|38.2237|0.64|1|False|
|ABS-LOCAL-2|budget_1.92|7.65982|15.754|1.92|1|False|
|ABS-LOCAL-3|economic|37.8589|70.3319|0|2|True|
|ABS-LOCAL-3|zero_budget|2.83773e-13|3.28182e-13|-2.84217e-14|0|True|
|ABS-LOCAL-3|budget_0.64|2.83773e-13|3.28182e-13|-1.42109e-14|0|True|
|ABS-LOCAL-3|budget_1.92|2.83773e-13|3.28182e-13|-1.42109e-14|0|True|
|ABS-REMOTE-1|economic|28|81.2779|0|2|False|
|ABS-REMOTE-1|zero_budget|8|26.7809|-8.52651e-14|1|False|
|ABS-REMOTE-1|budget_0.64|7.58483|29.4245|0.64|1|False|
|ABS-REMOTE-1|budget_1.92|7.22|30.1087|1.2024|1|False|
|ABS-REMOTE-2|economic|30.7998|107.77|0|2|False|
|ABS-REMOTE-2|zero_budget|7.919|50.7648|2.84217e-14|1|False|
|ABS-REMOTE-2|budget_0.64|7.50383|35.6627|0.64|1|False|
|ABS-REMOTE-2|budget_1.92|7.22|38.8609|1.34272|1|False|
|ABS-REMOTE-3|economic|37.6416|51.7391|0|2|False|
|ABS-REMOTE-3|zero_budget|8|8|-1.42109e-14|0|False|
|ABS-REMOTE-3|budget_0.64|7.88661|23.1817|0.64|1|False|
|ABS-REMOTE-3|budget_1.92|7.65982|22.9629|1.92|1|False|

## 解释范围

证书固定储能、EV和旧任务，仅回收弃电；证书不满足不代表完整模型不可修复。
证书最小参与数只在此直接吸收子类中成立，不是完整优化的参与数下界。
证书成本等于原计划剩余成本，是否满足事件后经济预算另行核验。
若证书在预算内，且完整优化证明最优，则应得到零总计划修改。
峰值参数在求解前冻结，保留所有案例，未进行结果导向调整。
准备、共享经济参照与各阶段开销单独保存；多阶段开销不能省略。
