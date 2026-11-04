# 数据来源、授权与口径声明

## 来源

| 项 | 内容 |
|---|---|
| 数据集 | Seed-Controlled Benchmark Instances for Dynamic Truck–Drone Delivery Dispatch (Shenzhen Qianhai Case) |
| 平台 | IEEE DataPort |
| DOI | [10.21227/3gxb-mj63](https://dx.doi.org/10.21227/3gxb-mj63) |
| 作者 | Jin Xin Cao (Inner Mongolia Academy of Science and Technology) |
| 发布 | 2026-09-30 |
| 授权 | **CC BY 4.0**（IEEE DataPort 页面 License 字段） |
| 引用 | 若用于论文/报告，请按 CC BY 4.0 标注上述 DOI 与作者 |

## 实际获取路径（重要，涉及可复现性）

1. 主数据集归档 `shenzhen_qianhai_truck_drone_dataset_zip`（53.1 KB，含 `scenario*.json`、`case_nodes.json`、`amap_raw/`）在页面上受 **IEEE SAML 登录**保护：`LOGIN TO ACCESS DATASET FILES` → `/saml_login/?idp=IEEEDataport`。本项目**未使用**任何账号凭据。
2. 页面同时提供一个**免登录的公开签名链接**：`DATASET SCRIPTS` → `build_case_script.zip`（9,409 B），内含 `build_case.py`（24,126 B）。该脚本自包含了该案例的**全部地理与随机参数**。
3. 页面「Research Article Link」指向的配套代码仓库 `https://github.com/IMUJason/robust-truck-drone-dispatch` 实测 **HTTP 404**（`git ls-remote` 亦返回 `Repository not found`），与网络/VPN 无关，是死链。因此 `build_case.py` 依赖的 `dagcd` 包无法取得，脚本本身不能直接执行。

**本项目做法**：`build_dataset.py` 用 `ast.literal_eval` **直接读取归档脚本里的常量**（不 `exec`、不复制粘贴硬编码），再用标准库复现脚本中明文写出的生成过程。原始脚本原样归档于 `ieee_build_case.py`，其 SHA-256 写入 `out/manifest.json`。这样任何人都可以在无账号、无网络、无第三方包的情况下复现全部输入。

## 什么来自公开数据，什么是本项目派生

| 类别 | 内容 |
|---|---|
| **公开参数（REAL）** | 27 个 WGS-84 节点坐标及中文名称（1 depot / 3 vertiport / 23 客户）、宝安机场 5 顶点禁飞区多边形、宝安体育馆临时禁飞区中心与 120 m 半边长、两个禁飞区的激活窗口（15:00–18:00 / 17:00–19:00）、分段泊松订单过程 λ=(0.09, 0.03, 0.14)、3 个固定 seed、07:00–19:00 仿真时域、拥堵分段表、雨季天气 Markov 链、道路绕行系数 1.724±0.537 |
| **按公开过程生成（REPRODUCED）** | `orders.csv` 的 233 行订单（含目的地、重量、服务时长、时间窗）。day0 用归档 seed `2026092302` 精确复现出 **52 单**，与 DataPort 摘要「52 online orders each」一致；day1–4 为 `--order-seed 1..4` 的变体日，按泊松波动为 39/55/48/39 单 |
| **本项目派生（DERIVED，非实测飞行）** | `trajectory_points.csv`：从发布的 3 个 vertiport 中选最近者起飞，按固定规则线性插值出 9 个采样点；巡航高度层 {45,60,75,90} m 由 `sha256(day, order_id)` 确定性选取；爬升/巡航/降落垂直剖面。**这是可复现的合成构造，不是真实无人机轨迹**，仅用于让空间/高度分析工具有可审计的输入 |

## 已知的口径差异（刻意且已记录）

1. **坐标框架**：原 benchmark 把所有节点坐标乘以绕行系数 `circuity=1.724` 换算成「道路等效米」用于路径优化；本项目做真实地理空间分析，坐标保留 WGS-84 / 等距圆柱真实米，**不做缩放**，绕行系数仅作为来源参数记录。
2. **临时禁飞区**：原脚本在缩放后的框架里用未缩放的 120 m 半边长构方形；本项目在 WGS-84 下按 120 真实米换算经纬度偏移。该区仅 240 m 见方，不影响任何结论。
3. **订单数**：摘要称每天 52 单；本复刻 day0 = 52，其余变体日按泊松过程自然波动。未做补齐/截断，因为那会破坏生成过程的可复现性。
4. **天气事实表**：原脚本的 `_sample_weather` 来自不可得的 `dagcd` 包，本项目**不生成**天气事实表，只在 `manifest.json` 保留公开的转移矩阵参数。评测题不涉及天气。

## 一个必须说明的数据局限

该实例的两个禁飞区与配送网络在**几何上零交集**：机场区经度 113.792–113.842，而全部 27 个节点与 2097 个轨迹点经度 ≥ 113.845；临时区 240 m 方形亦无轨迹穿过。激活窗口内虽有 1741 个轨迹点，但无一落在区内。

因此「轨迹–禁飞区冲突次数」在本数据集上**恒为 0**，对任何工具链都一样。本项目把这一发现作为数据质量结论报告，并另用**明确标注的合成探针轨迹**（`lowalt/engine/probes.py`）单独验证几何算法。合成探针**从不混入**业务评测题或业务结论。
