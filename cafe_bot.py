import json
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)
import logging
import glob
import asyncio

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния бота
MAIN_MENU, PROCESS_CHECKLIST, GET_REASON, GET_MEDIA, GET_COMMENTS = range(5)

class ChecklistBot:
    def __init__(self):
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.CONFIG_DIR = os.path.join(self.BASE_DIR, "config")
        self.REPORTS_DIR = os.path.join(self.BASE_DIR, "reports")
        self.MEDIA_DIR = os.path.join(self.BASE_DIR, "media")
        
        os.makedirs(self.CONFIG_DIR, exist_ok=True)
        os.makedirs(self.REPORTS_DIR, exist_ok=True)
        os.makedirs(self.MEDIA_DIR, exist_ok=True)
        
        self.MAX_MEDIA_AGE_DAYS = 7
        self.last_media_cleanup = datetime.min
        self.TIMEOUT = 30  # Таймаут в секундах для запросов к Telegram API

    def load_json(self, file_path, default=None):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"Не удалось загрузить {file_path}: {e}")
            return default if default is not None else {}

    def save_json(self, data, file_path):
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"Ошибка при сохранении {file_path}: {e}")
            return False

    def clean_old_media(self, force=False):
        """Очищает старые медиафайлы не чаще чем раз в час"""
        try:
            now = datetime.now()
            if not force and (now - self.last_media_cleanup) < timedelta(hours=1):
                return
                
            cutoff = now - timedelta(days=self.MAX_MEDIA_AGE_DAYS)
            deleted_files = 0
            
            for filepath in glob.glob(os.path.join(self.MEDIA_DIR, '*')):
                try:
                    file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if file_time < cutoff:
                        os.remove(filepath)
                        deleted_files += 1
                except Exception as e:
                    logger.warning(f"Ошибка при обработке файла {filepath}: {e}")
            
            self.last_media_cleanup = now
            logger.info(f"Очистка медиа: удалено {deleted_files} файлов")
        except Exception as e:
            logger.error(f"Ошибка при очистке медиа: {e}")

    async def save_media(self, media_file, media_type):
        """Сохраняет медиа с обработкой ошибок"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            ext = "jpg" if media_type == "photo" else "mp4"
            filename = f"media_{timestamp}.{ext}"
            filepath = os.path.join(self.MEDIA_DIR, filename)
            
            await media_file.download_to_drive(filepath)
            logger.info(f"Медиафайл сохранен: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"Ошибка сохранения медиа: {e}")
            return None

    async def safe_edit_message(self, query, text, reply_markup=None):
        """Безопасное редактирование сообщения с обработкой таймаутов"""
        try:
            await asyncio.wait_for(
                query.edit_message_text(
                    text=text,
                    reply_markup=reply_markup
                ),
                timeout=self.TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning("Таймаут при редактировании сообщения")
            raise
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}")
            raise

    async def safe_send_message(self, context, chat_id, text, reply_markup=None):
        """Безопасная отправка сообщения с обработкой таймаутов"""
        try:
            await asyncio.wait_for(
                context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup
                ),
                timeout=self.TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning("Таймаут при отправке сообщения")
            raise
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
            raise

    async def safe_answer_callback(self, query):
        """Безопасный ответ на callback с обработкой таймаутов"""
        try:
            await asyncio.wait_for(
                query.answer(),
                timeout=self.TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning("Таймаут при ответе на callback")
            raise
        except Exception as e:
            logger.error(f"Ошибка при ответе на callback: {e}")
            raise

    async def start(self, update: Update, context: CallbackContext) -> int:
        """Начало работы с ботом"""
        try:
            user = update.message.from_user if update.message else update.callback_query.from_user
            
            # Инициализация сессии с сохранением данных пользователя
            if 'session' not in context.user_data:
                context.user_data['session'] = {
                    'user_id': user.id,
                    'user_name': user.full_name or f"User_{user.id}",
                    'start_time': datetime.now().isoformat()
                }
            
            # Очищаем только временные данные чек-листа
            context.user_data['session'].update({
                'answers': {},
                'current_index': 0,
                'checklist_type': None,
                'checklist': None,
                'comments': None
            })
            
            keyboard = [
                [InlineKeyboardButton("📖 Открыть смену", callback_data='open_shift')],
                [InlineKeyboardButton("🔒 Закрыть смену", callback_data='close_shift')]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.callback_query:
                await self.safe_edit_message(
                    update.callback_query,
                    "☕ Добро пожаловать в CafeChecklistBot!\nВыберите действие:",
                    reply_markup
                )
            else:
                await self.safe_send_message(
                    context,
                    update.effective_chat.id,
                    "☕ Добро пожаловать в CafeChecklistBot!\nВыберите действие:",
                    reply_markup
                )
                
            return MAIN_MENU
        except Exception as e:
            logger.error(f"Ошибка в start: {e}", exc_info=True)
            await self.handle_error(update, context)
            return ConversationHandler.END

    async def start_checklist(self, update: Update, context: CallbackContext, checklist_type: str) -> int:
        """Начинает чек-лист"""
        try:
            query = update.callback_query
            await self.safe_answer_callback(query)
            
            session = context.user_data.setdefault('session', {})
            session.update({
                'checklist_type': checklist_type,
                'current_index': 0,
                'answers': {},
                'comments': None
            })
            
            # Загрузка чек-листа
            checklist_file = os.path.join(
                self.CONFIG_DIR,
                f"{checklist_type}_checklist.json"
            )
            
            checklist = self.load_json(checklist_file)
            if not checklist:
                await self.safe_edit_message(query, "⚠️ Чек-лист не найден")
                return ConversationHandler.END
                
            session['checklist'] = checklist
            return await self.show_question(update, context)
            
        except Exception as e:
            logger.error(f"Ошибка в start_checklist: {e}", exc_info=True)
            await self.handle_error(update, context)
            return ConversationHandler.END

    async def show_question(self, update: Update, context: CallbackContext) -> int:
        """Показывает текущий вопрос"""
        try:
            session = context.user_data.get('session', {})
            checklist = session.get('checklist', [])
            current_idx = session.get('current_index', 0)
            
            if current_idx >= len(checklist):
                await self.safe_send_message(
                    context,
                    update.effective_chat.id,
                    "💬 Пожалуйста, напишите любые комментарии к отчету (или нажмите /skip чтобы пропустить):"
                )
                return GET_COMMENTS
            
            question = checklist[current_idx]
            keyboard = [
                [
                    InlineKeyboardButton("✅ Выполнено", callback_data='done'),
                    InlineKeyboardButton("❌ Не выполнено", callback_data='not_done'),
                    InlineKeyboardButton("⬅️ Назад", callback_data='back') if current_idx > 0 else None
                ]
            ]
            # Удаляем None кнопки, если они есть
            keyboard = [[btn for btn in row if btn is not None] for row in keyboard]
            
            text = f"📋 Вопрос {current_idx+1}/{len(checklist)}:\n\n{question['question']}"
            if question.get('requires_media'):
                text += "\n\n📷 Требуется фото/видео отчет!"
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.callback_query:
                await self.safe_edit_message(
                    update.callback_query,
                    text,
                    reply_markup
                )
            else:
                await self.safe_send_message(
                    context,
                    update.effective_chat.id,
                    text,
                    reply_markup
                )
                    
            return PROCESS_CHECKLIST
        except Exception as e:
            logger.error(f"Ошибка в show_question: {e}", exc_info=True)
            await self.handle_error(update, context)
            return ConversationHandler.END

    async def handle_answer(self, update: Update, context: CallbackContext) -> int:
        """Обрабатывает ответ на вопрос"""
        try:
            query = update.callback_query
            await self.safe_answer_callback(query)
            
            session = context.user_data.get('session', {})
            current_idx = session.get('current_index', 0)
            checklist = session.get('checklist', [])
            
            if query.data == 'back':
                # Возвращаемся к предыдущему вопросу
                session['current_index'] = max(0, current_idx - 1)
                return await self.show_question(update, context)
            
            if current_idx >= len(checklist):
                return await self.finish_checklist(update, context)
                
            answer = query.data == 'done'
            question = checklist[current_idx]
            
            # Сохраняем ответ
            session['answers'][str(current_idx)] = {
                'question': question['question'],
                'answer': answer,
                'reason': None,
                'media': None
            }
            
            # Переходим к следующему вопросу
            session['current_index'] += 1
            
            if not answer:
                text = f"📋 Вопрос {current_idx+1}/{len(checklist)}:\n\n{question['question']}\n\n📝 Укажите причину:"
                await self.safe_edit_message(query, text)
                return GET_REASON
                
            if question.get('requires_media'):
                text = f"📋 Вопрос {current_idx+1}/{len(checklist)}:\n\n{question['question']}\n\n📷 Пожалуйста, прикрепите фото/видео отчет:"
                await self.safe_edit_message(query, text)
                return GET_MEDIA
                
            return await self.show_question(update, context)
            
        except Exception as e:
            logger.error(f"Ошибка в handle_answer: {e}", exc_info=True)
            await self.handle_error(update, context)
            return ConversationHandler.END

    async def handle_reason(self, update: Update, context: CallbackContext) -> int:
        """Обрабатывает причину"""
        try:
            reason = update.message.text.strip()
            if not reason:
                await self.safe_send_message(
                    context,
                    update.effective_chat.id,
                    "⚠️ Причина не может быть пустой. Пожалуйста, укажите причину."
                )
                return GET_REASON
                
            session = context.user_data.get('session', {})
            current_idx = session.get('current_index', 1) - 1
            
            if str(current_idx) in session.get('answers', {}):
                session['answers'][str(current_idx)]['reason'] = reason
                
            checklist = session.get('checklist', [])
            if current_idx < len(checklist) and checklist[current_idx].get('requires_media'):
                text = f"📋 Вопрос {current_idx+1}/{len(checklist)}:\n\n{checklist[current_idx]['question']}\n\n📷 Теперь прикрепите фото/видео отчет:"
                await self.safe_send_message(
                    context,
                    update.effective_chat.id,
                    text
                )
                return GET_MEDIA
                
            return await self.show_question(update, context)
            
        except Exception as e:
            logger.error(f"Ошибка в handle_reason: {e}", exc_info=True)
            await self.handle_error(update, context)
            return ConversationHandler.END

    async def handle_media(self, update: Update, context: CallbackContext) -> int:
        """Обрабатывает медиа"""
        try:
            session = context.user_data.get('session', {})
            current_idx = session.get('current_index', 1) - 1
            
            if update.message.photo:
                media_file = await update.message.photo[-1].get_file()
                media_type = "photo"
            elif update.message.video:
                media_file = await update.message.video.get_file()
                media_type = "video"
            else:
                text = f"📋 Вопрос {current_idx+1}/{len(session.get('checklist', []))}:\n\n{session['checklist'][current_idx]['question']}\n\n⚠️ Пожалуйста, отправьте фото или видео."
                await self.safe_send_message(
                    context,
                    update.effective_chat.id,
                    text
                )
                return GET_MEDIA
                
            media_path = await self.save_media(media_file, media_type)
            if media_path and str(current_idx) in session.get('answers', {}):
                session['answers'][str(current_idx)]['media'] = media_path
                
            return await self.show_question(update, context)
            
        except Exception as e:
            logger.error(f"Ошибка в handle_media: {e}", exc_info=True)
            await self.handle_error(update, context)
            return ConversationHandler.END

    async def handle_comments(self, update: Update, context: CallbackContext) -> int:
        """Обрабатывает комментарии к отчету"""
        try:
            if update.message.text.strip().lower() == '/skip':
                comments = "Нет комментариев"
            else:
                comments = update.message.text.strip()
                
            context.user_data['session']['comments'] = comments
            return await self.finish_checklist(update, context)
            
        except Exception as e:
            logger.error(f"Ошибка в handle_comments: {e}", exc_info=True)
            await self.handle_error(update, context)
            return ConversationHandler.END

    async def send_report_to_admin(self, context: CallbackContext, admin_id, user_data):
        """Отправляет отчет администратору с медиафайлами"""
        try:
            # Отправляем текстовый отчет
            report_text = self.format_report(user_data)
            await self.safe_send_message(
                context,
                admin_id,
                report_text
            )
            
            # Собираем все медиафайлы
            media_files = []
            for idx, answer in sorted(user_data.get('answers', {}).items(), key=lambda x: int(x[0])):
                if answer.get('media'):
                    media_path = answer['media']
                    try:
                        if os.path.exists(media_path):
                            if media_path.endswith(".jpg"):
                                media_files.append(InputMediaPhoto(media=open(media_path, 'rb')))
                            elif media_path.endswith(".mp4"):
                                media_files.append(InputMediaVideo(media=open(media_path, 'rb')))
                    except Exception as e:
                        logger.error(f"Ошибка обработки медиа {media_path}: {e}")
                        continue
            
            # Отправляем медиафайлы группами по 10
            if media_files:
                for i in range(0, len(media_files), 10):
                    try:
                        await asyncio.wait_for(
                            context.bot.send_media_group(
                                chat_id=admin_id,
                                media=media_files[i:i+10]
                            ),
                            timeout=self.TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Таймаут при отправке медиагруппы")
                        continue
                    except Exception as e:
                        logger.error(f"Ошибка отправки медиагруппы: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"Ошибка отправки отчета админу {admin_id}: {e}")
            raise

    async def finish_checklist(self, update: Update, context: CallbackContext) -> int:
        """Завершает чек-лист и отправляет отчет"""
        try:
            session = context.user_data.get('session', {})
            
            # Проверка данных
            if not session or not session.get('answers'):
                await self.safe_send_message(
                    context,
                    update.effective_chat.id,
                    "⚠️ Нет данных для отчета. Начните заново /start"
                )
                return ConversationHandler.END
                
            # Проверка причин
            for idx, answer in session['answers'].items():
                if not answer['answer'] and not answer.get('reason'):
                    await self.safe_send_message(
                        context,
                        update.effective_chat.id,
                        f"⚠️ Для вопроса {int(idx)+1} укажите причину"
                    )
                    return MAIN_MENU
                    
            # Сохранение отчета
            report = {
                'user_id': session.get('user_id'),
                'user_name': session.get('user_name', 'Неизвестный пользователь'),
                'checklist_type': session.get('checklist_type'),
                'date': datetime.now().isoformat(),
                'answers': session['answers'],
                'comments': session.get('comments', 'Нет комментариев')
            }
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_file = os.path.join(
                self.REPORTS_DIR,
                f"{report['user_name']}_{report['checklist_type']}_{timestamp}.json"
            )
            
            if self.save_json(report, report_file):
                logger.info(f"Отчет сохранен: {report_file}")
                
            # Отправка администраторам
            admin_ids = self.load_json(os.path.join(self.CONFIG_DIR, "admin_ids.json"), [])
            if admin_ids:
                for admin_id in admin_ids:
                    try:
                        await self.send_report_to_admin(context, admin_id, session)
                    except Exception as e:
                        logger.error(f"Ошибка отправки отчета админу {admin_id}: {e}")
            
            # Завершение
            keyboard = [
                [InlineKeyboardButton("📖 Открыть смену", callback_data='open_shift')],
                [InlineKeyboardButton("🔒 Закрыть смену", callback_data='close_shift')],
                [InlineKeyboardButton("🔄 Начать заново", callback_data='start')]
            ]
            
            await self.safe_send_message(
                context,
                update.effective_chat.id,
                "✅ Отчет успешно отправлен руководителю!",
                InlineKeyboardMarkup(keyboard)
            )
                
            # Очищаем только временные данные, сохраняя информацию о пользователе
            context.user_data['session'] = {
                'user_id': session.get('user_id'),
                'user_name': session.get('user_name', 'Неизвестный пользователь'),
                'start_time': datetime.now().isoformat()
            }
            
            return MAIN_MENU
            
        except Exception as e:
            logger.error(f"Ошибка в finish_checklist: {e}", exc_info=True)
            await self.handle_error(update, context)
            return ConversationHandler.END

    def format_report(self, session):
        """Форматирует отчет"""
        checklist_type = "открытия" if session.get('checklist_type') == "open" else "закрытия"
        report_text = f"📝 Отчет по чек-листу {checklist_type} смены\n"
        report_text += f"👤 Сотрудник: {session.get('user_name', 'Неизвестный пользователь')}\n"
        report_text += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        
        answers = session.get('answers', {})
        for idx, answer_data in sorted(answers.items(), key=lambda x: int(x[0])):
            status = "✅ Выполнено" if answer_data.get("answer", False) else "❌ Не выполнено"
            report_text += f"{int(idx)+1}. {answer_data.get('question', 'Без вопроса')}\n"
            report_text += f"   {status}\n"
            if not answer_data.get("answer", True) and answer_data.get("reason"):
                report_text += f"   Причина: {answer_data['reason']}\n"
            report_text += "\n"
        
        report_text += f"\n💬 Комментарии: {session.get('comments', 'Нет комментариев')}"
        
        return report_text

    async def handle_error(self, update: Update, context: CallbackContext):
        """Обработка ошибок"""
        try:
            if update.callback_query:
                try:
                    await self.safe_edit_message(
                        update.callback_query,
                        "⚠️ Ошибка. Попробуйте /start"
                    )
                except:
                    await self.safe_send_message(
                        context,
                        update.effective_chat.id,
                        "⚠️ Ошибка. Попробуйте /start"
                    )
            elif update.message:
                await self.safe_send_message(
                    context,
                    update.effective_chat.id,
                    "⚠️ Ошибка. Попробуйте /start"
                )
            if 'session' in context.user_data:
                context.user_data['session'] = {
                    'user_id': context.user_data['session'].get('user_id'),
                    'user_name': context.user_data['session'].get('user_name', 'Неизвестный пользователь'),
                    'start_time': datetime.now().isoformat()
                }
        except Exception as e:
            logger.error(f"Ошибка в handle_error: {e}", exc_info=True)

    def setup_handlers(self, application):
        """Настройка обработчиков"""
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', self.start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.start)
            ],
            states={
                MAIN_MENU: [
                    CallbackQueryHandler(
                        lambda u,c: self.start_checklist(u,c,'open'), 
                        pattern='^open_shift$'),
                    CallbackQueryHandler(
                        lambda u,c: self.start_checklist(u,c,'close'), 
                        pattern='^close_shift$'),
                    CallbackQueryHandler(self.start, pattern='^start$')
                ],
                PROCESS_CHECKLIST: [
                    CallbackQueryHandler(self.handle_answer, pattern='^(done|not_done|back)$')
                ],
                GET_REASON: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_reason)
                ],
                GET_MEDIA: [
                    MessageHandler(filters.PHOTO | filters.VIDEO, self.handle_media)
                ],
                GET_COMMENTS: [
                    MessageHandler(filters.TEXT | filters.COMMAND, self.handle_comments)
                ]
            },
            fallbacks=[CommandHandler('cancel', self.handle_error)],
            per_message=False
        )
        
        application.add_handler(conv_handler)
        application.add_error_handler(self.error_handler)

    async def error_handler(self, update: Update, context: CallbackContext):
        """Глобальный обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}", exc_info=True)
        await self.handle_error(update, context)

def main():
    try:
        logger.info("Запуск бота...")
        bot = ChecklistBot()
        
        # Проверка конфигурации
        config = bot.load_json(os.path.join(bot.CONFIG_DIR, "config.json"))
        if not config.get("bot_token"):
            raise ValueError("Токен бота не найден")
            
        # Очистка старых файлов при запуске
        bot.clean_old_media(force=True)
        
        # Увеличиваем таймауты для Application
        application = Application.builder().token(config["bot_token"]).read_timeout(30).write_timeout(30).connect_timeout(30).build()
        bot.setup_handlers(application)
        
        logger.info("Бот готов к работе")
        application.run_polling()
        
    except Exception as e:
        logger.critical(f"Ошибка запуска: {e}", exc_info=True)

if __name__ == '__main__':
    main()
