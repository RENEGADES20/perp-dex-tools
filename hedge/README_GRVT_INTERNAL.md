# GRVT 所内主子账户对冲脚本

这个脚本实现了 GRVT 交易所内部的主账户和子账户之间的对冲交易，用于**持续刷量**。

## 功能特点

- **主账户作为 Maker**：在主账户上下 post-only 限价单（maker 订单）
- **子账户作为 Taker**：在子账户上下市价单（taker 订单）进行对冲
- **持续刷量模式**：支持无限循环运行，持续刷交易量
- **买卖交替**：自动交替做多和做空，保持仓位平衡
- **可配置参数**：持仓时间、循环间隔可自定义
- **自动平仓**：每次对冲成功后自动平仓
- **实时统计**：显示总循环次数和总交易量
- **详细日志**：记录所有交易到 CSV 文件和日志文件

## 环境配置

### 1. 主账户配置文件 (account1.env)

```env
GRVT_TRADING_ACCOUNT_ID=your_main_account_id
GRVT_PRIVATE_KEY=your_main_private_key
GRVT_API_KEY=your_main_api_key
GRVT_ENVIRONMENT=prod
```

### 2. 子账户配置文件 (account2.env)

```env
GRVT_TRADING_ACCOUNT_ID=your_sub_account_id
GRVT_PRIVATE_KEY=your_sub_private_key
GRVT_API_KEY=your_sub_api_key
GRVT_ENVIRONMENT=prod
```

## 使用方法

### 🔥 持续刷量模式（推荐）

```bash
# 持续运行，按 Ctrl+C 停止
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --continuous

# 自定义持仓时间和循环间隔
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --continuous \
  --hold-time 15 \
  --wait-time 10
```

### 固定次数模式

```bash
# 运行 10 次后停止
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --iter 10 \
  --hold-time 5 \
  --wait-time 3
```

### 完整参数说明

```bash
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --main-env "F:\perp_tools\perp-dex-tools\account1.env" \
  --sub-env "F:\perp_tools\perp-dex-tools\account2.env" \
  --continuous \
  --hold-time 10 \
  --wait-time 5
```

### 参数说明

| 参数 | 说明 | 默认值 | 是否必填 |
|------|------|--------|----------|
| `--ticker` | 交易标的（如 BTC, ETH） | BTC | 否 |
| `--size` | 每笔订单的数量 | 无 | **是** |
| `--main-env` | 主账户环境变量文件路径 | `F:\perp_tools\perp-dex-tools\account1.env` | 否 |
| `--sub-env` | 子账户环境变量文件路径 | `F:\perp_tools\perp-dex-tools\account2.env` | 否 |
| `--continuous` | 启用持续运行模式（无限循环） | 否（固定次数） | 否 |
| `--iter` | 固定次数模式下的运行次数 | 1 | 否 |
| `--hold-time` | 持仓时间（秒） | 10 | 否 |
| `--wait-time` | 每次循环间隔时间（秒） | 5 | 否 |

## 工作流程

### 持续刷量模式流程

```
初始化 → 循环开始 → 买入对冲 → 持仓 → 平仓 → 等待间隔 → 卖出对冲 → 持仓 → 平仓 → 等待间隔 → 循环...
```

### 详细步骤

1. **初始化**
   - 加载主账户环境变量
   - 加载子账户环境变量
   - 初始化两个 GRVT 客户端
   - 建立 WebSocket 连接

2. **循环 1 - 买入对冲（做多）**
   - 主账户：下 BUY post-only 限价单（maker）
   - 等待主账户订单成交
   - 子账户：立即下 SELL 市价单（taker）对冲
   - 持仓指定时间（`--hold-time`）
   - 主账户：平仓（SELL 市价单）
   - 子账户：平仓（BUY 市价单）
   - 等待间隔（`--wait-time`）

3. **循环 2 - 卖出对冲（做空）**
   - 主账户：下 SELL post-only 限价单（maker）
   - 等待主账户订单成交
   - 子账户：立即下 BUY 市价单（taker）对冲
   - 持仓指定时间（`--hold-time`）
   - 主账户：平仓（BUY 市价单）
   - 子账户：平仓（SELL 市价单）
   - 等待间隔（`--wait-time`）

4. **重复循环**
   - 买卖方向自动交替（BUY → SELL → BUY → SELL...）
   - 持续运行模式：无限循环直到按 Ctrl+C 停止
   - 固定次数模式：完成指定次数后停止

## 日志文件

所有日志文件保存在 `logs/` 目录下：

- `logs/grvt_{ticker}_internal_hedge_log.txt` - 详细运行日志
- `logs/grvt_{ticker}_internal_hedge_trades.csv` - 交易记录 CSV

### CSV 格式

```csv
account,timestamp,side,price,quantity
MAIN,2025-01-01T12:00:00Z,buy,50000.5,0.001
SUB,2025-01-01T12:00:01Z,sell,50001.0,0.001
```

## 示例

### 示例 1：持续刷量（推荐）

```bash
# 24小时持续刷量，每次持仓10秒，循环间隔5秒
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --continuous
```

**预期效果：**
- 每个完整循环约 30 秒（开仓+持仓10秒+平仓+间隔5秒）
- 每小时约 120 个循环
- 24小时约 2880 个循环
- 总交易量：0.001 × 2 × 2880 = 5.76 BTC

### 示例 2：快速刷量

```bash
# 持仓5秒，循环间隔2秒，快速刷量
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --continuous \
  --hold-time 5 \
  --wait-time 2
```

**预期效果：**
- 每个完整循环约 15 秒
- 每小时约 240 个循环
- 交易频率更高

### 示例 3：固定次数测试

```bash
# 运行 5 次后停止，用于测试
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --iter 5 \
  --hold-time 3 \
  --wait-time 2
```

### 示例 4：ETH 持续刷量

```bash
python hedge/hedge_mode_grvt_internal.py \
  --ticker ETH \
  --size 0.01 \
  --continuous \
  --hold-time 15 \
  --wait-time 10
```

### 示例 5：自定义环境文件路径

```bash
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --main-env "/path/to/main.env" \
  --sub-env "/path/to/sub.env" \
  --continuous
```

## 注意事项

### 持续刷量相关

1. **停止脚本**：
   - 按 `Ctrl+C` 优雅停止（会完成当前循环）
   - 不要直接关闭终端窗口

2. **账户余额**：
   - 确保两个账户都有足够的余额支持长时间运行
   - 建议余额至少能支持 100 个循环

3. **参数调优**：
   - `--hold-time`：持仓时间越长，交易频率越低，但更安全
   - `--wait-time`：循环间隔时间，建议至少 3-5 秒，避免API限流
   - 建议先用 `--iter 5` 测试几次，确认一切正常后再用 `--continuous`

4. **监控运行状态**：
   - 脚本会实时显示总循环次数和总交易量
   - 定期查看 CSV 日志确认交易正常
   - 建议使用 `screen` 或 `tmux` 在后台运行

### 一般注意事项

5. **环境文件路径**：
   - Windows 用户需要使用反斜杠 `\` 或原始字符串 `r"path"`
   - Linux/Mac 用户使用正斜杠 `/`

6. **最小订单量**：
   - 确保 `--size` 参数大于交易所最小订单量
   - BTC 通常最小为 0.0001

7. **API 权限**：
   - 确保 API 密钥有交易权限
   - 确保两个账户都有足够的余额

8. **网络连接**：
   - 需要稳定的网络连接
   - WebSocket 断线会自动重连
   - 建议使用稳定的服务器或VPS运行

9. **风险提示**：
   - 这是一个自动交易脚本，请在测试环境充分测试后再使用
   - 市价单可能有滑点
   - 持续刷量会产生手续费成本
   - 请根据自己的风险承受能力设置订单大小

## 故障排除

### 问题 1：找不到环境文件

```
FileNotFoundError: Environment file not found: ...
```

**解决方案**：检查环境文件路径是否正确，确保文件存在

### 问题 2：GRVT 凭证缺失

```
ValueError: GRVT credentials missing in ...
```

**解决方案**：检查环境文件中是否包含所有必需的 GRVT 凭证

### 问题 3：订单量太小

```
ValueError: Order quantity is less than min quantity
```

**解决方案**：增加 `--size` 参数值

## 技术支持

如有问题，请查看：
- 主项目 README.md
- GRVT 官方文档
- 项目 GitHub Issues

## 许可证

本项目遵循主项目的许可证条款。
