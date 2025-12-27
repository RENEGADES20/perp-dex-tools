import asyncio
import json
import signal
import logging
import os
import sys
import time
import argparse
import traceback
import csv
from decimal import Decimal
from typing import Tuple
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchanges.grvt import GrvtClient
from datetime import datetime
import pytz


class Config:
    """Simple config class to wrap dictionary for GRVT client."""
    def __init__(self, config_dict):
        for key, value in config_dict.items():
            setattr(self, key, value)


class GrvtInternalHedgeBot:
    """Trading bot that places post-only orders on main GRVT account and hedges with market orders on sub GRVT account."""

    def __init__(self, ticker: str, order_quantity: Decimal, main_env_path: str, sub_env_path: str,
                 iterations: int = 1, continuous: bool = False, hold_time: int = 10, wait_time: int = 5):
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.main_env_path = main_env_path
        self.sub_env_path = sub_env_path
        self.iterations = iterations
        self.continuous = continuous  # 持续运行模式
        self.hold_time = hold_time  # 持仓时间（秒）
        self.wait_time = wait_time  # 每次循环间隔时间（秒）

        # 统计信息
        self.total_cycles = 0  # 总循环次数
        self.total_volume = Decimal('0')  # 总交易量

        # Initialize logging to file
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/grvt_{ticker}_internal_hedge_log.txt"
        self.csv_filename = f"logs/grvt_{ticker}_internal_hedge_trades.csv"

        # Initialize CSV file with headers if it doesn't exist
        self._initialize_csv_file()

        # Setup logger
        self.logger = logging.getLogger(f"grvt_internal_hedge_bot_{ticker}")
        self.logger.setLevel(logging.INFO)

        # Clear any existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # Disable verbose logging from external libraries
        logging.getLogger('urllib3').setLevel(logging.CRITICAL)
        logging.getLogger('requests').setLevel(logging.CRITICAL)
        logging.getLogger('websockets').setLevel(logging.CRITICAL)
        logging.getLogger('pysdk').setLevel(logging.CRITICAL)
        logging.getLogger('pysdk.grvt_ccxt').setLevel(logging.CRITICAL)
        logging.getLogger('pysdk.grvt_ccxt_ws').setLevel(logging.CRITICAL)
        logging.getLogger('pysdk.grvt_ccxt_logging_selector').setLevel(logging.CRITICAL)
        logging.getLogger('pysdk.grvt_ccxt_env').setLevel(logging.CRITICAL)

        # Disable root logger propagation to prevent external logs
        logging.getLogger().setLevel(logging.CRITICAL)

        # Create file handler with UTF-8 encoding
        file_handler = logging.FileHandler(self.log_filename, encoding='utf-8')
        file_handler.setLevel(logging.INFO)

        # Create console handler with UTF-8 encoding for Windows compatibility
        import io
        utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        console_handler = logging.StreamHandler(utf8_stdout)
        console_handler.setLevel(logging.INFO)

        # Create different formatters for file and console
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_formatter = logging.Formatter('%(levelname)s:%(name)s:%(message)s')

        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)

        # Add handlers to logger
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        # Prevent propagation to root logger to avoid duplicate messages and external logs
        self.logger.propagate = False

        # State management
        self.stop_flag = False

        # Main account GRVT state
        self.main_client = None
        self.main_contract_id = None
        self.main_tick_size = None
        self.main_order_status = None
        self.main_position = Decimal('0')

        # Sub account GRVT state
        self.sub_client = None
        self.sub_contract_id = None
        self.sub_tick_size = None
        self.sub_order_status = None
        self.sub_position = Decimal('0')

        # Order execution tracking
        self.main_order_filled = False
        self.sub_order_filled = False
        self.waiting_for_sub_fill = False

        # Current order details
        self.current_main_order_id = None
        self.current_main_filled_size = Decimal('0')
        self.current_main_price = Decimal('0')

    def shutdown(self, signum=None, frame=None):
        """Graceful shutdown handler."""
        self.stop_flag = True
        self.logger.info("\n🛑 Stopping...")

        # Close logging handlers properly
        for handler in self.logger.handlers[:]:
            try:
                handler.close()
                self.logger.removeHandler(handler)
            except Exception:
                pass

    def _initialize_csv_file(self):
        """Initialize CSV file with headers if it doesn't exist."""
        if not os.path.exists(self.csv_filename):
            with open(self.csv_filename, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['account', 'timestamp', 'side', 'price', 'quantity'])

    def log_trade_to_csv(self, account: str, side: str, price: str, quantity: str):
        """Log trade details to CSV file."""
        timestamp = datetime.now(pytz.UTC).isoformat()

        with open(self.csv_filename, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                account,
                timestamp,
                side,
                price,
                quantity
            ])

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def load_env_file(self, env_path: str):
        """Load environment variables from a specific .env file."""
        if not os.path.exists(env_path):
            raise FileNotFoundError(f"Environment file not found: {env_path}")
        load_dotenv(env_path, override=True)
        self.logger.info(f"✅ Loaded environment from: {env_path}")

    def initialize_grvt_client(self, env_path: str, account_name: str):
        """Initialize a GRVT client with specific environment file."""
        # Load the environment file
        self.load_env_file(env_path)

        # Get GRVT credentials from environment
        trading_account_id = os.getenv('GRVT_TRADING_ACCOUNT_ID')
        private_key = os.getenv('GRVT_PRIVATE_KEY')
        api_key = os.getenv('GRVT_API_KEY')

        if not all([trading_account_id, private_key, api_key]):
            raise ValueError(f"GRVT credentials missing in {env_path}")

        # Create config for GRVT client
        config_dict = {
            'ticker': self.ticker,
            'contract_id': '',  # Will be set when we get contract info
            'quantity': self.order_quantity,
            'tick_size': Decimal('0.01'),  # Will be updated when we get contract info
            'close_order_side': 'sell',  # Default
            'direction': 'buy',  # Default direction, not used in our implementation
            'log_to_console': True  # Enable console logging for debugging
        }

        # Wrap in Config class for GRVT client
        config = Config(config_dict)

        # Initialize GRVT client
        client = GrvtClient(config)

        self.logger.info(f"✅ GRVT {account_name} client initialized successfully")
        return client

    async def get_grvt_contract_info(self, client) -> Tuple[str, Decimal]:
        """Get GRVT contract ID and tick size."""
        if not client:
            raise Exception("GRVT client not initialized")

        contract_id, tick_size = await client.get_contract_attributes()

        if self.order_quantity < client.config.quantity:
            raise ValueError(
                f"Order quantity is less than min quantity: {self.order_quantity} < {client.config.quantity}")

        return contract_id, tick_size

    async def get_grvt_position(self, client) -> Decimal:
        """Get GRVT position."""
        if not client:
            raise Exception("GRVT client not initialized")

        return await client.get_account_positions()

    def round_to_tick(self, price: Decimal, tick_size: Decimal) -> Decimal:
        """Round price to tick size."""
        if tick_size is None:
            return price
        return (price / tick_size).quantize(Decimal('1')) * tick_size

    async def fetch_grvt_bbo_prices(self, client, contract_id: str) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/ask prices from GRVT using REST API."""
        if not client:
            raise Exception("GRVT client not initialized")

        best_bid, best_ask = await client.fetch_bbo_prices(contract_id)

        return best_bid, best_ask

    async def place_main_post_only_order(self, side: str, quantity: Decimal):
        """Place a post-only order on main GRVT account."""
        if not self.main_client:
            raise Exception("Main GRVT client not initialized")

        self.main_order_status = None
        self.main_order_filled = False

        # Get current best bid/ask for logging
        try:
            best_bid, best_ask = await self.fetch_grvt_bbo_prices(self.main_client, self.main_contract_id)
            self.logger.info(f"[MAIN] [OPEN] [{side}] Current BBO: bid={best_bid}, ask={best_ask}")
            self.logger.info(f"[MAIN] [OPEN] [{side}] Placing GRVT POST-ONLY order: {quantity}")
        except Exception as e:
            self.logger.warning(f"[MAIN] Could not fetch BBO: {e}")
            self.logger.info(f"[MAIN] [OPEN] [{side}] Placing GRVT POST-ONLY order: {quantity}")

        # Place the order
        order_result = await self.main_client.place_open_order(
            contract_id=self.main_contract_id,
            quantity=quantity,
            direction=side.lower()
        )

        if order_result is None:
            raise Exception(f"Failed to place main order: place_open_order returned None (max retries exceeded or order rejected)")

        if not order_result.success:
            raise Exception(f"Failed to place main order: {order_result.error_message}")

        self.current_main_order_id = order_result.order_id
        self.logger.info(f"[MAIN] Order placed: {order_result.order_id} @ {order_result.price}")

        # Wait for order to fill
        start_time = time.time()
        while not self.main_order_filled and not self.stop_flag:
            if time.time() - start_time > 60:
                self.logger.error(f"[MAIN] Timeout waiting for order fill")
                # Cancel the order
                await self.main_client.cancel_order(self.current_main_order_id)
                raise Exception("[MAIN] Order fill timeout")

            await asyncio.sleep(0.5)

            # Check if we need to cancel and replace the order
            best_bid, best_ask = await self.fetch_grvt_bbo_prices(self.main_client, self.main_contract_id)

            if time.time() - start_time > 10:
                should_cancel = False
                if side.lower() == 'buy':
                    if order_result.price < best_bid:
                        should_cancel = True
                else:  # sell
                    if order_result.price > best_ask:
                        should_cancel = True

                if should_cancel:
                    try:
                        self.logger.info(f"[MAIN] Canceling and replacing order")
                        cancel_result = await self.main_client.cancel_order(self.current_main_order_id)
                        if cancel_result.success:
                            # Place new order
                            order_result = await self.main_client.place_open_order(
                                contract_id=self.main_contract_id,
                                quantity=quantity,
                                direction=side.lower()
                            )
                            if order_result.success:
                                self.current_main_order_id = order_result.order_id
                                start_time = time.time()
                    except Exception as e:
                        self.logger.error(f"[MAIN] Error canceling/replacing order: {e}")

        return self.current_main_order_id

    async def place_sub_market_order(self, side: str, quantity: Decimal):
        """Place a market order on sub GRVT account."""
        if not self.sub_client:
            raise Exception("Sub GRVT client not initialized")

        self.sub_order_status = None
        self.sub_order_filled = False
        self.logger.info(f"[SUB] [HEDGE] [{side}] Placing GRVT MARKET order: {quantity}")

        # Place market order
        await self.sub_client.place_market_order(
            contract_id=self.sub_contract_id,
            quantity=quantity,
            side=side.lower()
        )

        # Wait a bit for the order to process
        await asyncio.sleep(2)

        self.sub_order_filled = True
        self.logger.info(f"[SUB] Market order executed")

    def handle_main_order_update(self, order_data):
        """Handle main GRVT account order updates from WebSocket."""
        if order_data.get('contract_id') != self.main_contract_id:
            return

        try:
            order_id = order_data.get('order_id')
            status = order_data.get('status')
            side = order_data.get('side', '').lower()
            filled_size = Decimal(order_data.get('filled_size', '0'))
            size = Decimal(order_data.get('size', '0'))
            price = order_data.get('price', '0')

            if status == 'CANCELED' and filled_size > 0:
                status = 'FILLED'

            # Handle the order update
            if status == 'FILLED' and self.main_order_status != 'FILLED':
                if side == 'buy':
                    self.main_position += filled_size
                else:
                    self.main_position -= filled_size

                self.logger.info(f"[{order_id}] [MAIN] [{status}]: {filled_size} @ {price}")
                self.main_order_status = status
                self.main_order_filled = True

                # Store filled order details
                self.current_main_filled_size = filled_size
                self.current_main_price = Decimal(price)

                # Log trade to CSV
                self.log_trade_to_csv(
                    account='MAIN',
                    side=side,
                    price=str(price),
                    quantity=str(filled_size)
                )

                # Trigger sub account hedge
                self.waiting_for_sub_fill = True

            elif self.main_order_status != 'FILLED':
                if status == 'OPEN':
                    self.logger.info(f"[{order_id}] [MAIN] [{status}]: {size} @ {price}")
                else:
                    self.logger.info(f"[{order_id}] [MAIN] [{status}]: {filled_size} @ {price}")
                self.main_order_status = status

        except Exception as e:
            self.logger.error(f"Error handling main order update: {e}")

    def handle_sub_order_update(self, order_data):
        """Handle sub GRVT account order updates from WebSocket."""
        if order_data.get('contract_id') != self.sub_contract_id:
            return

        try:
            order_id = order_data.get('order_id')
            status = order_data.get('status')
            side = order_data.get('side', '').lower()
            filled_size = Decimal(order_data.get('filled_size', '0'))
            size = Decimal(order_data.get('size', '0'))
            price = order_data.get('price', '0')

            if status == 'CANCELED' and filled_size > 0:
                status = 'FILLED'

            # Handle the order update
            if status == 'FILLED' and self.sub_order_status != 'FILLED':
                if side == 'buy':
                    self.sub_position += filled_size
                else:
                    self.sub_position -= filled_size

                self.logger.info(f"[{order_id}] [SUB] [{status}]: {filled_size} @ {price}")
                self.sub_order_status = status
                self.sub_order_filled = True

                # Log trade to CSV
                self.log_trade_to_csv(
                    account='SUB',
                    side=side,
                    price=str(price),
                    quantity=str(filled_size)
                )

            elif self.sub_order_status != 'FILLED':
                if status == 'OPEN':
                    self.logger.info(f"[{order_id}] [SUB] [{status}]: {size} @ {price}")
                else:
                    self.logger.info(f"[{order_id}] [SUB] [{status}]: {filled_size} @ {price}")
                self.sub_order_status = status

        except Exception as e:
            self.logger.error(f"Error handling sub order update: {e}")

    async def setup_main_websocket(self):
        """Setup main account GRVT websocket for order updates."""
        if not self.main_client:
            raise Exception("Main GRVT client not initialized")

        self.main_client.setup_order_update_handler(self.handle_main_order_update)
        await self.main_client.connect()
        self.logger.info("✅ Main account GRVT WebSocket connection established")

    async def setup_sub_websocket(self):
        """Setup sub account GRVT websocket for order updates."""
        if not self.sub_client:
            raise Exception("Sub GRVT client not initialized")

        self.sub_client.setup_order_update_handler(self.handle_sub_order_update)
        await self.sub_client.connect()
        self.logger.info("✅ Sub account GRVT WebSocket connection established")

    async def close_position(self, side: str):
        """Close positions on both accounts."""
        self.logger.info(f"💤 Holding position for {self.hold_time} seconds...")
        await asyncio.sleep(self.hold_time)

        self.logger.info(f"📊 Closing positions...")

        # Determine close sides (opposite of open)
        if side.lower() == 'buy':
            main_close_side = 'sell'
            sub_close_side = 'buy'
        else:
            main_close_side = 'buy'
            sub_close_side = 'sell'

        # Get current positions
        main_pos = await self.get_grvt_position(self.main_client)
        sub_pos = await self.get_grvt_position(self.sub_client)

        self.logger.info(f"Main position: {main_pos}, Sub position: {sub_pos}")

        # Close main account position with market order
        if abs(main_pos) > 0:
            self.logger.info(f"[MAIN] [CLOSE] [{main_close_side}] Closing position: {abs(main_pos)}")
            await self.main_client.place_market_order(
                contract_id=self.main_contract_id,
                quantity=abs(main_pos),
                side=main_close_side
            )
            await asyncio.sleep(1)

        # Close sub account position with market order
        if abs(sub_pos) > 0:
            self.logger.info(f"[SUB] [CLOSE] [{sub_close_side}] Closing position: {abs(sub_pos)}")
            await self.sub_client.place_market_order(
                contract_id=self.sub_contract_id,
                quantity=abs(sub_pos),
                side=sub_close_side
            )
            await asyncio.sleep(1)

        self.logger.info(f"✅ Positions closed")

    async def trading_loop(self):
        """Main trading loop implementing the GRVT internal hedge strategy."""
        self.logger.info(f"🚀 Starting GRVT internal hedge bot for {self.ticker}")

        # Initialize main account client
        try:
            self.logger.info("Initializing main account client...")
            self.main_client = self.initialize_grvt_client(self.main_env_path, "MAIN")
            self.main_contract_id, self.main_tick_size = await self.get_grvt_contract_info(self.main_client)
            self.logger.info(f"Main account contract info loaded - {self.main_contract_id}")
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize main account: {e}")
            return

        # Initialize sub account client
        try:
            self.logger.info("Initializing sub account client...")
            self.sub_client = self.initialize_grvt_client(self.sub_env_path, "SUB")
            self.sub_contract_id, self.sub_tick_size = await self.get_grvt_contract_info(self.sub_client)
            self.logger.info(f"Sub account contract info loaded - {self.sub_contract_id}")
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize sub account: {e}")
            return

        # Setup WebSockets
        try:
            await self.setup_main_websocket()
            await self.setup_sub_websocket()
            await asyncio.sleep(3)  # Wait for WebSocket connections to stabilize
        except Exception as e:
            self.logger.error(f"❌ Failed to setup WebSockets: {e}")
            return

        # Trading iterations
        iteration = 0
        current_side = 'buy'  # 开始方向，之后会交替

        # 显示运行模式
        if self.continuous:
            self.logger.info("📊 Running in CONTINUOUS mode - Press Ctrl+C to stop")
            self.logger.info(f"⚙️ Settings: hold_time={self.hold_time}s, wait_time={self.wait_time}s")
        else:
            self.logger.info(f"📊 Running {self.iterations} iterations")
            self.logger.info(f"⚙️ Settings: hold_time={self.hold_time}s, wait_time={self.wait_time}s")

        # 主循环：持续模式下无限循环，否则按迭代次数
        while not self.stop_flag:
            # 检查是否达到迭代次数限制（仅在非持续模式下）
            if not self.continuous and iteration >= self.iterations:
                break

            iteration += 1
            self.total_cycles += 1

            self.logger.info("=" * 60)
            if self.continuous:
                self.logger.info(f"🔄 Cycle {iteration} | Total Volume: {self.total_volume} {self.ticker}")
            else:
                self.logger.info(f"🔄 Cycle {iteration}/{self.iterations} | Total Volume: {self.total_volume} {self.ticker}")
            self.logger.info(f"📈 Current Direction: {current_side.upper()}")
            self.logger.info("=" * 60)

            try:
                # Reset flags
                self.main_order_filled = False
                self.sub_order_filled = False
                self.waiting_for_sub_fill = False

                # 1. Place post-only order on main account (maker)
                await self.place_main_post_only_order(current_side, self.order_quantity)

                # 2. Wait for main order to fill and then hedge with sub account
                if self.waiting_for_sub_fill:
                    # Determine hedge side (opposite of main order)
                    hedge_side = 'sell' if current_side.lower() == 'buy' else 'buy'

                    # Place market order on sub account (taker)
                    await self.place_sub_market_order(hedge_side, self.current_main_filled_size)

                    # 更新总交易量（双边都算）
                    self.total_volume += self.current_main_filled_size * 2

                # 3. Hold position and then close
                await self.close_position(current_side)

                self.logger.info(f"✅ Cycle {iteration} completed")

                # 交替方向：buy <-> sell
                current_side = 'sell' if current_side == 'buy' else 'buy'

                # 等待间隔时间再进行下一次循环
                if self.wait_time > 0 and not self.stop_flag:
                    if self.continuous or iteration < self.iterations:
                        self.logger.info(f"⏳ Waiting {self.wait_time}s before next cycle...")
                        await asyncio.sleep(self.wait_time)

            except Exception as e:
                self.logger.error(f"⚠️ Error in cycle {iteration}: {e}")
                self.logger.error(f"⚠️ Full traceback: {traceback.format_exc()}")
                if not self.continuous:
                    break
                else:
                    # 持续模式下出错后等待一段时间再继续
                    self.logger.info(f"⏳ Error occurred, waiting 30s before retry...")
                    await asyncio.sleep(30)

        # 最终统计
        self.logger.info("=" * 60)
        self.logger.info(f"🎉 Trading completed!")
        self.logger.info(f"📊 Total Cycles: {self.total_cycles}")
        self.logger.info(f"📊 Total Volume: {self.total_volume} {self.ticker}")
        self.logger.info("=" * 60)

    async def run(self):
        """Run the hedge bot."""
        self.setup_signal_handlers()

        try:
            await self.trading_loop()
        except KeyboardInterrupt:
            self.logger.info("\n🛑 Received interrupt signal...")
        finally:
            self.logger.info("🔄 Cleaning up...")

            # Disconnect WebSockets
            if self.main_client:
                try:
                    await self.main_client.disconnect()
                except Exception as e:
                    self.logger.error(f"Error disconnecting main client: {e}")

            if self.sub_client:
                try:
                    await self.sub_client.disconnect()
                except Exception as e:
                    self.logger.error(f"Error disconnecting sub client: {e}")

            self.shutdown()


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='GRVT Internal Hedge Trading Bot (Main Account Maker + Sub Account Taker)')
    parser.add_argument('--ticker', type=str, default='BTC',
                        help='Ticker symbol (default: BTC)')
    parser.add_argument('--size', type=str, required=True,
                        help='Number of tokens to buy/sell per order')
    parser.add_argument('--main-env', type=str,
                        default=r'F:\perp_tools\perp-dex-tools\account1.env',
                        help='Path to main account .env file')
    parser.add_argument('--sub-env', type=str,
                        default=r'F:\perp_tools\perp-dex-tools\account2.env',
                        help='Path to sub account .env file')
    parser.add_argument('--iter', type=int, default=1,
                        help='Number of iterations to run (ignored if --continuous is set) (default: 1)')
    parser.add_argument('--continuous', action='store_true',
                        help='Run continuously until stopped (Ctrl+C to stop)')
    parser.add_argument('--hold-time', type=int, default=10,
                        help='Seconds to hold position before closing (default: 10)')
    parser.add_argument('--wait-time', type=int, default=5,
                        help='Seconds to wait between cycles (default: 5)')

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()

    # Create bot instance
    bot = GrvtInternalHedgeBot(
        ticker=args.ticker,
        order_quantity=Decimal(args.size),
        main_env_path=args.main_env,
        sub_env_path=args.sub_env,
        iterations=args.iter,
        continuous=args.continuous,
        hold_time=args.hold_time,
        wait_time=args.wait_time
    )

    # Run the bot
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
