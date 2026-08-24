"""
数据导出模块

提供将股票数据导出为 CSV 文件的功能
"""

import sys
from pathlib import Path

# 添加项目根目录到路径（.../TradingAgents-CN）
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import pandas as pd
import logging
from typing import List, Optional
from zstock.data_management.query_service import get_data_query_service

logger = logging.getLogger(__name__)


class DataExporter:
    """数据导出器"""

    def __init__(self, output_dir: str = "exports"):
        """初始化导出器"""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.query_service = get_data_query_service()

    async def export_stock_info_to_csv(
        self,
        symbols: List[str],
        output_filename: Optional[str] = None
    ) -> str:
        """
        导出股票信息为 CSV 文件

        Args:
            symbols: 股票代码列表 (e.g., ["600000", "SH600000", "000001"])
            output_filename: 输出文件名 (默认: stock_info.csv)

        Returns:
            输出文件的完整路径

        Raises:
            ValueError: 无法获取股票数据
        """
        if not output_filename:
            output_filename = "stock_info.csv"

        output_path = self.output_dir / output_filename

        # 收集所有股票信息
        stock_data_list = []
        for symbol in symbols:
            try:
                stock_info, source = await self.query_service.get_stock_info(symbol)
                if stock_info:
                    stock_info['_source'] = source  # 记录数据源
                    stock_data_list.append(stock_info)
                    logger.info(f"✅ 获取股票信息: {symbol} (来自 {source})")
            except ValueError as e:
                logger.warning(f"⚠️ 无法获取股票信息: {symbol} - {e}")
                continue

        if not stock_data_list:
            raise ValueError(f"❌ 无法获取任何股票的信息")

        # 转换为 DataFrame
        df = pd.DataFrame(stock_data_list)

        # 导出为 CSV
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
        logger.info(f"✅ 数据已导出到: {output_path}")
        logger.info(f"📊 导出了 {len(df)} 条股票记录，共 {len(df.columns)} 列")

        return str(output_path)

    async def export_multiple_symbols_to_separate_files(
        self,
        symbols: List[str],
        output_dir: Optional[str] = None
    ) -> List[str]:
        """
        导出多个股票的信息，每个股票一个文件

        Args:
            symbols: 股票代码列表
            output_dir: 输出目录 (默认使用初始化时的目录)

        Returns:
            导出的文件路径列表
        """
        output_folder = Path(output_dir) if output_dir else self.output_dir
        output_folder.mkdir(parents=True, exist_ok=True)

        exported_files = []
        for symbol in symbols:
            try:
                stock_info, source = await self.query_service.get_stock_info(symbol)
                if stock_info:
                    df = pd.DataFrame([stock_info])
                    filename = f"stock_info_{symbol}.csv"
                    filepath = output_folder / filename
                    df.to_csv(filepath, index=False, encoding='utf-8-sig')
                    exported_files.append(str(filepath))
                    logger.info(f"✅ 导出文件: {filepath}")
            except ValueError as e:
                logger.warning(f"⚠️ 跳过 {symbol}: {e}")
                continue

        logger.info(f"📊 成功导出 {len(exported_files)} 个文件")
        return exported_files


async def main():
    """测试导出功能"""
    try:
        # 导入并初始化 MongoDB 客户端
        from zstock.common.utils.db_utils import init_zstock_database, close_zstock_database
        from app.core.database import get_database

        # 初始化 MongoDB
        await init_zstock_database()
        logger.info("✅ MongoDB 已初始化")

        exporter = DataExporter(output_dir="")

        # 测试股票代码列表
        symbols = ["600000", "000001", "000858", "SH600519"]

        # 导出为单个 CSV 文件
        output_path = await exporter.export_stock_info_to_csv(
            symbols,
            output_filename="stock_info.csv"
        )
        print(f"\n✅ 数据导出成功: {output_path}")

        # 读取并显示部分数据
        df = pd.read_csv(output_path)
        print(f"\n📊 导出的数据预览 (前3行):")
        print(df.head(3))
        print(f"\n列名: {list(df.columns)}")

    except Exception as e:
        logger.error(f"导出失败: {e}")
        print(f"❌ 导出失败: {e}")
    finally:
        # 关闭 MongoDB 连接
        try:
            from zstock.common.utils.db_utils import close_zstock_database
            await close_zstock_database()
            logger.info("✅ MongoDB 连接已关闭")
        except:
            pass


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(main())
