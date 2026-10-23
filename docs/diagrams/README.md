# 主要用例图

依据[需求分析](../brief/惠策协同_需求分析与功能范围.md)和[功能实现逻辑与接口协作约定](../IMPLEMENTATION_AND_INTERFACE.md)绘制，范围为 FR-01～FR-09。这是功能设计，不代表业务已经实现。

- [可编辑 PlantUML 源文件](./main-use-cases.puml)
- [PNG 图片](./main-use-cases.png)：适合插入报告。
- [SVG 矢量图](./main-use-cases.svg)：适合放大、排版。

## 角色和关系

三类外部参与者：企业用户、政策标注与审核员、受限演示人员。共14个用例，覆盖画像、机会检索、四态分析、原文依据、补问、失效重算、分类清单、报告、政策导入/抽取/审核/发布、引用校验与协作轨迹。

实线表示参与者使用用例，不表示流程先后。虚线 `include` 从基础用例指向必须执行的子用例；`extend` 从条件扩展指向被扩展用例。补问仅在必要事实不足时触发；重新核验必需再次执行适用性分析；抽取、修正与发布均须校验引用。

协调器、采集/条款解析/企业画像/解释与准备智能体、规则引擎和引用校验器都在系统内部，不作为外部参与者。图底部注明这项约束；多智能体调用顺序和参数见功能实现文档第2.8、6节，用例图不承担架构图或时序图的作用。

| 需求 | 图中对应目标 |
| --- | --- |
| FR-01 | 导入政策原文与新版本、发布审核通过版本 |
| FR-02 | 抽取政策条件、审核并修正规则、校验原文引用 |
| FR-03 | 维护企业画像与事实证据 |
| FR-04 | 查找政策机会、分析政策适用性、查看逐条判断依据 |
| FR-05 | 补充缺失事实 |
| FR-06 | 查看分类准备清单 |
| FR-07 | 重新核验失效结论 |
| FR-08 | 查看与下载分析报告、查看原文依据 |
| FR-09 | 查看智能体审计轨迹（额外管理授权） |

## 本地 VS Code 使用

2026-10-23 实际查到 `jebbs.plantuml 2.18.1`，插件自带 PlantUML 1.2024.3，Java 为17.0.12；内置 Graphviz 检查通过。此次通过命令行复用**该插件自带的 plantuml.jar**完成本地语法检查和导出，未调用远程渲染服务。已执行 `code --reuse-window --goto <源文件>:1`，退出码0；没有通过界面工具确认插件预览面板是否已打开，也没有执行 VS Code 内部的导出命令。

在 VS Code 打开 `.puml` 后按 **Alt+D** 预览；需要重新导出时在命令面板选择 **PlantUML: Export Current Diagram**。这两个操作来自本机插件自带 README。若插件配置为远程服务而你希望保持本地渲染，将 `plantuml.render` 选为 `Local`；此次未改动你的 VS Code 设置。

本次实际导出方式（PowerShell，从 `huice/` 运行；插件升级后需更新 jar 路径）：

```powershell
$plantumlJar = 'C:\Users\刘佳熙\.vscode\extensions\jebbs.plantuml-2.18.1\plantuml.jar'
java '-Djava.awt.headless=true' -jar $plantumlJar -charset UTF-8 -checkonly 'docs\diagrams\main-use-cases.puml'
java '-Djava.awt.headless=true' -jar $plantumlJar -charset UTF-8 -tpng 'docs\diagrams\main-use-cases.puml'
java '-Djava.awt.headless=true' -jar $plantumlJar -charset UTF-8 -tsvg 'docs\diagrams\main-use-cases.puml'
```

语法检查与两种导出均成功，并查看 PNG 确认中文可读、角色在系统边界外、关系线与标签可辨。修改源文件后重新导出两种图片，避免源图不一致。

文件职责：M3 维护此目录的业务含义及需求映射，M1/M2 审核实现边界。图片由 `.puml` 导出，不直接修改图片中的文字。
