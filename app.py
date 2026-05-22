# =====================================================
# CLI
# =====================================================
from rag.rag import RAGPipeline
from utils.logger import logger

if __name__ == "__main__":
    print("\nRAG 知识库问答系统正在初始化...\n")
    pipeline = RAGPipeline()

    print("\n系统已就绪 (输入 'exit' 退出, 'new' 开启新会话)\n")

    session_id = "cli-default"
    while True:
        try:
            q = input("\n问题：")
            if q.lower() in ["exit", "quit", "退出"]:
                logger.info("系统退出")
                print("\n再见！")
                break

            if q.lower() == "new":
                import uuid
                session_id = f"cli-{uuid.uuid4().hex[:8]}"
                print(f"\n已开启新会话: {session_id}\n")
                continue

            if not q.strip():
                print("请输入有效问题")
                continue

            ans = pipeline.ask(q, session_id=session_id)
            print("\n回答：")
            print(ans)

        except KeyboardInterrupt:
            logger.info("用户中断系统")
            print("\n\n再见！")
            break
        except Exception as e:
            logger.error(f"系统错误: {e}", exc_info=True)
            print(f"\n系统错误: {str(e)}")
            print("请重试或联系管理员")