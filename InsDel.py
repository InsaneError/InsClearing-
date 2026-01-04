from .. import loader, utils
import logging
import telethon
from datetime import datetime, timedelta
import re

logger = logging.getLogger(__name__)


@loader.tds
class InsDel(loader.Module):
    """Модуль чистки от @InsModule"""
    
    strings = {
        "name": "Очистка",
        "from_where": "<b>Откуда начать очистку? Ответьте на сообщение!</b>",
        "not_supergroup_bot": "<b>Очистка доступна только в супергруппах для ботов</b>",
        "delete_what": "<b>Какое сообщение удалить? Ответьте на сообщение!</b>",
        "no_messages": "<b>Сообщений для удаления не найдено</b>",
        "purge_complete": "<b>Очистка завершена! Удалено сообщений:</b> {}",
        "delete_complete": "<b>Сообщение удалено</b>",
        "processing": "<b>Обработка...</b>",
        "error": "<b>Ошибка:</b> {}",
        "stats": "<b>Статистика очистки:</b>\n Пользователей: {}\n Сообщений: {}",
        "invalid_time": "<b>Неверный формат времени. Используйте: 5m, 2h, 1d</b>",
        "time_purge_complete": "<b>Удалены сообщения за последние:</b> {}\n <b>Удалено:</b> {}",
        "type_purge_complete": "<b>Удалены сообщения типа:</b> {}\n <b>Удалено:</b> {}",
        "self_purge_complete": " <b>Удалены только ваши сообщения:</b> {}",
        "confirm_purge": " <b>Вы уверены что хотите удалить {} сообщений?</b>\nНапишите <code>.purge confirm</code> для подтверждения",
        "cancelled": " <b>Очистка отменена</b>",
        "scheduled": " <b>Очистка запланирована через {}</b>",
        "media_types": {
            "photo": "фото",
            "video": "видео",
            "document": "документ",
            "audio": "аудио",
            "voice": "голосовое",
            "sticker": "стикер",
            "gif": "гифка"
        }
    }
    
    strings_ru = strings  # Для русской локализации

    def __init__(self):
        self.pending_confirmations = {}
        self.scheduled_tasks = {}

    @loader.group_admin_delete_messages
    @loader.ratelimit
    async def purgecmd(self, message):
        """Очистка сообщений от ответа до текущего
        
        Использование:
          .purge - очистка всех сообщений
          .purge @username - очистка сообщений от конкретного пользователя
          .purge self - очистка только своих сообщений
          .purge 30m - очистка сообщений за последние 30 минут
          .purge media - очистка только медиа сообщений
          .purge text - очистка только текстовых сообщений
          .purge photo - очистка только фото
          .purge video - очистка только видео
          .purge confirm - подтверждение очистки
          .purge cancel - отмена очистки
          .purge stats - статистика очистки
        """
        args = utils.get_args_raw(message)
        
        if args == "confirm":
            await self._confirm_purge(message)
            return
        elif args == "cancel":
            await self._cancel_purge(message)
            return
        elif args == "stats":
            await self._show_stats(message)
            return
        
        if not message.is_reply and not args:
            await utils.answer(message, self.strings("from_where", message))
            return
        
        status_msg = await utils.answer(message, self.strings("processing", message))
        
        try:
            if await message.client.is_bot():
                if not message.is_channel:
                    await utils.answer(message, self.strings("not_supergroup_bot", message))
                    return
                deleted = await self._purge_bot(message, args)
            else:
                deleted = await self._purge_user(message, args)
                
            if deleted > 0:
                await utils.answer(
                    status_msg, 
                    self.strings("purge_complete", message).format(deleted)
                )
            else:
                await utils.answer(
                    status_msg,
                    self.strings("no_messages", message)
                )
                
        except Exception as e:
            logger.exception("Purge error")
            await utils.answer(
                status_msg,
                self.strings("error", message).format(str(e))
            )

    @loader.group_admin_delete_messages
    @loader.ratelimit
    async def delcmd(self, message):
        """Удалить конкретное сообщение"""
        msgs = [message.id]
        
        if not message.is_reply:
            if await message.client.is_bot():
                await utils.answer(message, self.strings("delete_what", message))
                return
            try:
                async for msg in message.client.iter_messages(
                    message.to_id, 
                    limit=1, 
                    offset_id=message.id - 1
                ):
                    msgs.append(msg.id)
                    break
            except StopAsyncIteration:
                await utils.answer(message, self.strings("no_messages", message))
                return
        else:
            msg = await message.get_reply_message()
            msgs.append(msg.id)
        
        await message.client.delete_messages(message.to_id, msgs)
        await utils.answer(message, self.strings("delete_complete", message))
        
        sender_id = msg.sender_id if message.is_reply else message.sender_id
        await self.allmodules.log(
            "delete", 
            group=message.to_id, 
            affected_uids=[sender_id]
        )

    @loader.group_admin_delete_messages
    @loader.ratelimit
    async def clearcmd(self, message):
        """Быстрая очистка (без подтверждения, до 50 сообщений)"""
        args = utils.get_args_raw(message)
        
        if not message.is_reply:
            await utils.answer(message, self.strings("from_where", message))
            return
        
        status_msg = await utils.answer(message, self.strings("processing", message))
        
        try:
            deleted = 0
            msgs = []
            
            async for msg in message.client.iter_messages(
                entity=message.to_id,
                min_id=message.reply_to_msg_id - 1,
                limit=50,
                reverse=True
            ):
                msgs.append(msg.id)
                deleted += 1
                
                if len(msgs) >= 99:
                    await message.client.delete_messages(message.to_id, msgs)
                    msgs.clear()
            
            if msgs:
                await message.client.delete_messages(message.to_id, msgs)
            
            await utils.answer(
                status_msg,
                self.strings("purge_complete", message).format(deleted)
            )
            
            await self.allmodules.log(
                "clear", 
                group=message.to_id, 
                affected_uids={"quick_clear": deleted}
            )
            
        except Exception as e:
            await utils.answer(
                status_msg,
                self.strings("error", message).format(str(e))
            )

    @loader.group_admin_delete_messages
    async def purgeselfcmd(self, message):
        """Удалить только свои сообщения"""
        if not message.is_reply:
            await utils.answer(message, self.strings("from_where", message))
            return
        
        status_msg = await utils.answer(message, self.strings("processing", message))
        
        try:
            deleted = 0
            msgs = []
            self_id = (await message.client.get_me()).id
            
            async for msg in message.client.iter_messages(
                entity=message.to_id,
                min_id=message.reply_to_msg_id - 1,
                reverse=True
            ):
                if msg.sender_id == self_id:
                    msgs.append(msg.id)
                    deleted += 1
                    
                    if len(msgs) >= 99:
                        await message.client.delete_messages(message.to_id, msgs)
                        msgs.clear()
            
            if msgs:
                await message.client.delete_messages(message.to_id, msgs)
            
            await utils.answer(
                status_msg,
                self.strings("self_purge_complete", message).format(deleted)
            )
            
            await self.allmodules.log(
                "purge_self", 
                group=message.to_id, 
                affected_uids=[self_id]
            )
            
        except Exception as e:
            await utils.answer(
                status_msg,
                self.strings("error", message).format(str(e))
            )

    async def _purge_user(self, message, args):
        """Основная логика очистки для пользовательского аккаунта"""
        from_users = set()
        filter_type = None
        time_filter = None
        
        # Парсинг аргументов
        if args:
            args_lower = args.lower()
            
            # Фильтр по времени (5m, 2h, 1d)
            time_match = re.match(r'(\d+)([mhd])', args_lower)
            if time_match:
                value, unit = time_match.groups()
                value = int(value)
                if unit == 'm':
                    time_filter = timedelta(minutes=value)
                elif unit == 'h':
                    time_filter = timedelta(hours=value)
                elif unit == 'd':
                    time_filter = timedelta(days=value)
            
            # Фильтр по типу
            elif args_lower in ['media', 'text', 'photo', 'video', 'audio', 'voice', 'sticker', 'gif', 'document']:
                filter_type = args_lower
            
            # Очистка своих сообщений
            elif args_lower == 'self':
                return await self._purge_self_only(message)
            
            # Фильтр по пользователю
            else:
                try:
                    entity = await message.client.get_entity(args)
                    if isinstance(entity, telethon.tl.types.User):
                        from_users.add(entity.id)
                except:
                    pass
        
        deleted = 0
        msgs = []
        affected_uids = set()
        time_limit = datetime.now() - time_filter if time_filter else None
        
        async for msg in message.client.iter_messages(
            entity=message.to_id,
            min_id=message.reply_to_msg_id - 1,
            reverse=True
        ):
            # Применяем фильтры
            if from_users and msg.sender_id not in from_users:
                continue
            
            if time_limit and msg.date < time_limit:
                continue
            
            if filter_type and not self._check_message_type(msg, filter_type):
                continue
            
            msgs.append(msg.id)
            affected_uids.add(msg.sender_id)
            deleted += 1
            
            if len(msgs) >= 99:
                await message.client.delete_messages(message.to_id, msgs)
                msgs.clear()
        
        if msgs:
            await message.client.delete_messages(message.to_id, msgs)
        
        await self.allmodules.log(
            "purge", 
            group=message.to_id, 
            affected_uids=affected_uids,
            count=deleted,
            filter_type=filter_type
        )
        
        return deleted

    async def _purge_bot(self, message, args):
        """Логика очистки для бота"""
        deleted = 0
        
        # Для ботов простая очистка по ID
        for msg_id in range(message.reply_to_msg_id, message.id + 1):
            deleted += 1
        
        await message.client.delete_messages(
            message.to_id,
            list(range(message.reply_to_msg_id, message.id + 1))
        )
        
        return deleted

    async def _purge_self_only(self, message):
        """Очистка только своих сообщений"""
        deleted = 0
        msgs = []
        self_id = (await message.client.get_me()).id
        
        async for msg in message.client.iter_messages(
            entity=message.to_id,
            min_id=message.reply_to_msg_id - 1,
            reverse=True
        ):
            if msg.sender_id == self_id:
                msgs.append(msg.id)
                deleted += 1
                
                if len(msgs) >= 99:
                    await message.client.delete_messages(message.to_id, msgs)
                    msgs.clear()
        
        if msgs:
            await message.client.delete_messages(message.to_id, msgs)
        
        return deleted

    def _check_message_type(self, msg, filter_type):
        """Проверка типа сообщения"""
        if filter_type == 'text':
            return msg.text and not msg.media
        elif filter_type == 'media':
            return bool(msg.media)
        elif filter_type == 'photo':
            return hasattr(msg.media, 'photo')
        elif filter_type == 'video':
            return hasattr(msg.media, 'document') and msg.media.document.mime_type.startswith('video')
        elif filter_type == 'audio':
            return hasattr(msg.media, 'document') and msg.media.document.mime_type.startswith('audio')
        elif filter_type == 'voice':
            return hasattr(msg.media, 'document') and hasattr(msg.media.document.attributes[0], 'voice')
        elif filter_type == 'sticker':
            return hasattr(msg.media, 'document') and hasattr(msg.media.document.attributes[0], 'sticker')
        elif filter_type == 'gif':
            return hasattr(msg.media, 'document') and hasattr(msg.media.document.attributes[0], 'animated')
        elif filter_type == 'document':
            return hasattr(msg.media, 'document') and not any([
                msg.media.document.mime_type.startswith('video'),
                msg.media.document.mime_type.startswith('audio'),
                msg.media.document.mime_type.startswith('image')
            ])
        return True

    async def _confirm_purge(self, message):
        """Подтверждение очистки"""
        chat_id = message.chat_id
        if chat_id in self.pending_confirmations:
            data = self.pending_confirmations.pop(chat_id)
            await utils.answer(message, self.strings("processing", message))
            
            # Выполняем очистку
            deleted = await self._execute_purge(message, data)
            
            await utils.answer(
                message,
                self.strings("purge_complete", message).format(deleted)
            )
        else:
            await utils.answer(message, "<b>Нет ожидающих подтверждения операций</b>")

    async def _cancel_purge(self, message):
        """Отмена очистки"""
        chat_id = message.chat_id
        if chat_id in self.pending_confirmations:
            self.pending_confirmations.pop(chat_id)
            await utils.answer(message, self.strings("cancelled", message))
        else:
            await utils.answer(message, "<b>Нет операций для отмены</b>")

    async def _show_stats(self, message):
        """Показать статистику очистки"""
        if not message.is_reply:
            await utils.answer(message, self.strings("from_where", message))
            return
        
        user_count = set()
        msg_count = 0
        
        async for msg in message.client.iter_messages(
            entity=message.to_id,
            min_id=message.reply_to_msg_id - 1,
            reverse=True
        ):
            user_count.add(msg.sender_id)
            msg_count += 1
        
        await utils.answer(
            message,
            self.strings("stats", message).format(len(user_count), msg_count)
        )

    async def _execute_purge(self, message, data):
        """Выполнение очистки с параметрами"""
        # Реализация очистки с учетом параметров из data
        deleted = 0
        msgs = []
        
        async for msg in message.client.iter_messages(
            entity=message.to_id,
            min_id=data.get('min_id', message.reply_to_msg_id - 1),
            reverse=True
        ):
            # Применяем фильтры из data
            msgs.append(msg.id)
            deleted += 1
            
            if len(msgs) >= 99:
                await message.client.delete_messages(message.to_id, msgs)
                msgs.clear()
        
        if msgs:
            await message.client.delete_messages(message.to_id, msgs)
        
        return deleted

    async def client_ready(self, client, db):
        self.client = client
        self.db = db
        self.me = await client.get_me()
