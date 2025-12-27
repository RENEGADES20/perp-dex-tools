# GRVT 所内主子账户对冲脚本

这个脚本实现了 GRVT 交易所内部的主账户和子账户之间的对冲交易。

## 功能特点

- **主账户作为 Maker**：在主账户上下 post-only 限价单（maker 订单）
- **子账户作为 Taker**：在子账户上下市价单（taker 订单）进行对冲
- **自动平仓**：对冲成功后持仓 10 秒，然后自动平仓
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

### 基本命令

```bash
# Windows 上运行
python hedge\hedge_mode_grvt_internal.py --ticker BTC --size 0.001

# Linux/Mac 上运行
python hedge/hedge_mode_grvt_internal.py --ticker BTC --size 0.001
```

### 完整参数说明

```bash
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --main-env "F:\perp_tools\perp-dex-tools\account1.env" \
  --sub-env "F:\perp_tools\perp-dex-tools\account2.env" \
  --iter 5
```

### 参数说明

- `--ticker`: 交易标的（如 BTC, ETH）（默认：BTC）
- `--size`: 每笔订单的数量（必填）
- `--main-env`: 主账户环境变量文件路径（默认：`F:\perp_tools\perp-dex-tools\account1.env`）
- `--sub-env`: 子账户环境变量文件路径（默认：`F:\perp_tools\perp-dex-tools\account2.env`）
- `--iter`: 运行迭代次数（默认：1）

## 工作流程

1. **初始化**
   - 加载主账户环境变量
   - 加载子账户环境变量
   - 初始化两个 GRVT 客户端
   - 建立 WebSocket 连接

2. **开仓阶段**
   - 主账户：下 post-only 限价单（maker）
   - 等待主账户订单成交
   - 子账户：立即下市价单（taker）对冲

3. **持仓阶段**
   - 持仓 10 秒

4. **平仓阶段**
   - 主账户：平仓（市价单）
   - 子账户：平仓（市价单）

5. **循环**
   - 根据 `--iter` 参数重复上述流程

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

### 示例 1：单次对冲

```bash
python hedge/hedge_mode_grvt_internal.py --ticker BTC --size 0.001 --iter 1
```

### 示例 2：多次迭代

```bash
python hedge/hedge_mode_grvt_internal.py --ticker ETH --size 0.01 --iter 10
```

### 示例 3：自定义环境文件路径

```bash
python hedge/hedge_mode_grvt_internal.py \
  --ticker BTC \
  --size 0.001 \
  --main-env "/path/to/main.env" \
  --sub-env "/path/to/sub.env" \
  --iter 5
```

## 注意事项

1. **环境文件路径**：
   - Windows 用户需要使用反斜杠 `\` 或原始字符串 `r"path"`
   - Linux/Mac 用户使用正斜杠 `/`

2. **最小订单量**：
   - 确保 `--size` 参数大于交易所最小订单量
   - BTC 通常最小为 0.0001

3. **API 权限**：
   - 确保 API 密钥有交易权限
   - 确保两个账户都有足够的余额

4. **网络连接**：
   - 需要稳定的网络连接
   - WebSocket 断线会自动重连

5. **风险提示**：
   - 这是一个自动交易脚本，请在测试环境充分测试后再使用
   - 市价单可能有滑点
   - 请勿在生产环境使用过大的订单量

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
