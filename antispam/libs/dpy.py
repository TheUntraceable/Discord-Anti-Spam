import ast
import datetime
import logging
from string import Template
from typing import Union, Optional
from unittest.mock import AsyncMock

import discord

from antispam import PropagateFailure, LogicError, MissingGuildPermissions
from antispam.abc import Lib
from antispam.dataclasses import Message, Member, Guild
from antispam.dataclasses.propagate_data import PropagateData
from tests.mocks import MockedChannel

log = logging.getLogger(__name__)


# noinspection PyProtocol
class DPY(Lib):
    def __init__(self, handler):
        self.handler = handler

    def substitute_args(
        self,
        message: str,
        original_message: discord.Message,
        warn_count: int,
        kick_count: int,
    ) -> str:
        return Template(message).safe_substitute(
            {
                "MENTIONUSER": original_message.author.mention,
                "USERNAME": original_message.author.display_name,
                "USERID": original_message.author.id,
                "BOTNAME": original_message.guild.me.display_name,
                "BOTID": original_message.guild.me.id,
                "GUILDID": original_message.guild.id,
                "GUILDNAME": original_message.guild.name,
                "TIMESTAMPNOW": datetime.datetime.now().strftime(
                    "%I:%M:%S %p, %d/%m/%Y"
                ),
                "TIMESTAMPTODAY": datetime.datetime.now().strftime("%d/%m/%Y"),
                "WARNCOUNT": warn_count,
                "KICKCOUNT": kick_count,
                "USERAVATAR": original_message.author.avatar_url,
                "BOTAVATAR": original_message.guild.me.avatar_url,
                "GUILDICON": original_message.guild.icon_url,
            }
        )

    def embed_to_string(self, embed: discord.Embed) -> str:
        content = ""
        embed = embed.to_dict()

        if "title" in embed:
            content += f"{embed['title']}\n"

        if "description" in embed:
            content += f"{embed['description']}\n"

        if "footer" in embed:
            if "text" in embed["footer"]:
                content += f"{embed['footer']['text']}\n"

        if "author" in embed:
            content += f"{embed['author']['name']}\n"

        if "fields" in embed:
            for field in embed["fields"]:
                content += f"{field['name']}\n{field['value']}\n"

        return content

    def dict_to_embed(
        self, data: dict, message: discord.Message, warn_count: int, kick_count: int
    ):
        allowed_avatars = ["$USERAVATAR", "$BOTAVATAR", "$GUILDICON"]

        if "title" in data:
            data["title"] = self.substitute_args(
                data["title"], message, warn_count, kick_count
            )

        if "description" in data:
            data["description"] = self.substitute_args(
                data["description"], message, warn_count, kick_count
            )

        if "footer" in data:
            if "text" in data["footer"]:
                data["footer"]["text"] = self.substitute_args(
                    data["footer"]["text"], message, warn_count, kick_count
                )

            if "icon_url" in data["footer"]:
                if data["footer"]["icon_url"] in allowed_avatars:
                    data["footer"]["icon_url"] = self.substitute_args(
                        data["footer"]["icon_url"], message, warn_count, kick_count
                    )

        if "author" in data:
            # name 'should' be required
            data["author"]["name"] = self.substitute_args(
                data["author"]["name"], message, warn_count, kick_count
            )

            if "icon_url" in data["author"]:
                if data["author"]["icon_url"] in allowed_avatars:
                    data["author"]["icon_url"] = self.substitute_args(
                        data["author"]["icon_url"], message, warn_count, kick_count
                    )

        if "fields" in data:
            for field in data["fields"]:
                name = self.substitute_args(
                    field["name"], message, warn_count, kick_count
                )
                value = self.substitute_args(
                    field["value"], message, warn_count, kick_count
                )
                field["name"] = name
                field["value"] = value

                if "inline" not in field:
                    field["inline"] = True

        if "timestamp" in data:
            data["timestamp"] = message.created_at.isoformat()

        if "colour" in data:
            data["color"] = data["colour"]

        data["type"] = "rich"

        return discord.Embed.from_dict(data)

    def transform_message(
        self,
        item: Union[str, dict],
        message: discord.Message,
        warn_count: int,
        kick_count: int,
    ):
        if isinstance(item, str):
            return self.substitute_args(item, message, warn_count, kick_count)

        return self.dict_to_embed(item, message, warn_count, kick_count)

    def visualizer(
        self,
        content: str,
        message: discord.Message,
        warn_count: int = 1,
        kick_count: int = 2,
    ):
        if content.startswith("{"):
            content = ast.literal_eval(content)

        return self.transform_message(content, message, warn_count, kick_count)

    async def check_message_can_be_propagated(
        self, message: discord.Message
    ) -> PropagateData:
        if not isinstance(message, (discord.Message, AsyncMock)):
            raise PropagateFailure(
                data={"status": "Expected message of type discord.Message"}
            )

        # Ensure we only moderate actual guild messages
        if not message.guild:
            log.debug("Message was not in a guild")
            raise PropagateFailure(data={"status": "Ignoring messages from dm's"})

        # The bot is immune to spam
        if message.author.id == self.handler.bot.user.id:
            log.debug("Message was from myself")
            raise PropagateFailure(
                data={"status": "Ignoring messages from myself (the bot)"}
            )

        if isinstance(message.author, discord.User):
            log.warning(f"Given message with an author of type User")

        # Return if ignored bot
        if self.handler.options.ignore_bots and message.author.bot:
            log.debug("I ignore bots, and this is a bot message: %s", message.author.id)
            raise PropagateFailure(data={"status": "Ignoring messages from bots"})

        # Return if ignored member
        if message.author.id in self.handler.options.ignored_members:
            log.debug(
                "The user who sent this message is ignored: %s", message.author.id
            )
            raise PropagateFailure(
                data={"status": f"Ignoring this member: {message.author.id}"}
            )

        # Return if ignored channel
        if (
            message.channel.id in self.handler.options.ignored_channels
            or message.channel.name in self.handler.options.ignored_channels
        ):
            log.debug("%s is ignored", message.channel)
            raise PropagateFailure(
                data={"status": f"Ignoring this channel: {message.channel.id}"}
            )

        # Return if member has an ignored role
        try:
            user_roles = [role.id for role in message.author.roles]
            user_roles.extend([role.name for role in message.author.roles])
            for item in user_roles:
                if item in self.handler.options.ignored_roles:
                    log.debug("%s is a part of ignored roles", item)
                    raise PropagateFailure(
                        data={"status": f"Ignoring this role: {item}"}
                    )
        except AttributeError:
            log.warning(
                "Could not compute ignored_roles for %s(%s)",
                message.author.name,
                message.author.id,
            )

        # Return if ignored guild
        if message.guild.id in self.handler.options.ignored_guilds:
            log.debug("%s is an ignored guild", message.guild.id)
            raise PropagateFailure(
                data={"status": f"Ignoring this guild: {message.guild.id}"}
            )

        perms = message.guild.me.guild_permissions
        has_perms = perms.kick_members and perms.ban_members

        return PropagateData(
            guild_id=message.guild.id,
            member_name=message.author.name,
            member_id=message.author.id,
            has_perms_to_make_guild=has_perms,
        )

    def create_message(self, message: discord.Message) -> Message:
        if not bool(message.content and message.content.strip()):
            if not message.embeds:
                raise LogicError

            embed = message.embeds[0]
            if not isinstance(embed, discord.Embed):
                raise LogicError

            if embed.type.lower() != "rich":
                raise LogicError

            content = self.embed_to_string(embed)
        else:
            content = message.clean_content

        if self.handler.options.delete_zero_width_chars:
            content = (
                content.replace("u200B", "")
                .replace("u200C", "")
                .replace("u200D", "")
                .replace("u200E", "")
                .replace("u200F", "")
                .replace("uFEFF", "")
            )

        return Message(
            id=message.id,
            channel_id=message.channel.id,
            guild_id=message.guild.id,
            author_id=message.author.id,
            content=content,
        )

    async def send_guild_log(
        self,
        guild,
        message: Union[str, discord.Embed],
        delete_after_time,
        original_channel: Union[discord.abc.GuildChannel, discord.abc.PrivateChannel],
    ) -> None:
        try:
            if not guild.log_channel_id:
                log.debug(
                    "%s has no log channel set, defaulting to original channel",
                    guild.id,
                )
                channel = original_channel
            else:
                channel = guild.log_channel_id

                channel = self.handler.bot.get_channel(channel)
                if not channel:
                    channel = await self.handler.bot.fetch_channel(channel)

            if isinstance(message, str):
                await channel.send(message, delete_after=delete_after_time)
            else:
                await channel.send(embed=message)

            log.debug("Sent message to log channel in %s", guild.id)
        except discord.HTTPException:
            log.error("Failed to send log message.\n" f"Guild: %s\n", guild.id)

    async def punish_member(
        self,
        original_message: discord.Message,
        member: Member,
        internal_guild: Guild,
        user_message,
        guild_message,
        is_kick: bool,
        user_delete_after: int = None,
        channel_delete_after: int = None,
    ):
        guild = original_message.guild
        author = original_message.author

        # Check we have perms to punish
        perms = guild.me.guild_permissions
        if not perms.kick_members and is_kick:
            member._in_guild = True
            member.kick_count -= 1
            raise MissingGuildPermissions(
                f"I need kick perms to punish someone in {guild.name}"
            )

        elif not perms.ban_members and not is_kick:
            member._in_guild = True
            member.kick_count -= 1
            raise MissingGuildPermissions(
                f"I need ban perms to punish someone in {guild.name}"
            )

        # We also check they don't own the guild, since ya know...
        elif guild.owner_id == member.id:
            member._in_guild = True
            member.kick_count -= 1
            raise MissingGuildPermissions(
                f"I cannot punish {author.display_name}({author.id}) "
                f"because they own this guild. ({guild.name})"
            )

        # Ensure we can actually punish the user, for this
        # we just check our top role is higher then them
        elif guild.me.top_role.position < author.top_role.position:
            log.warning(
                "I might not be able to punish %s(%s) in %s(%s) "
                "because they are higher then me, which means I could lack the ability to kick/ban them.",
                author.display_name,
                member.id,
                guild.name,
                guild.id,
            )

        sent_message: Optional[discord.Message] = None
        try:
            if isinstance(user_message, discord.Embed):
                sent_message = await author.send(
                    embed=user_message, delete_after=user_delete_after
                )
            else:
                sent_message = await author.send(
                    user_message, delete_after=user_delete_after
                )

        except discord.HTTPException:
            await self.send_guild_log(
                guild=internal_guild,
                message=f"Sending a message to {author.mention} about their {'kick' if is_kick else 'ban'} failed.",
                delete_after_time=channel_delete_after,
            )
            log.warning(
                "Failed to message User: (%s) about {'kick' if is_kick else 'ban'}",
                author.id,
            )

        # Even if we can't tell them they are being punished
        # We still need to punish them, so try that
        try:
            if is_kick:
                await guild.kick(
                    member, reason="Automated punishment from DPY Anti-Spam."
                )
                log.info("Kicked User: (%s)", member.id)
            else:
                await guild.ban(
                    member, reason="Automated punishment from DPY Anti-Spam."
                )
                log.info("Banned User: (%s)", member.id)

        except discord.Forbidden as e:
            # In theory we send the failed punishment method
            # here, although we check first so I think its fine
            # to remove it from this part
            raise e from None

        except discord.HTTPException:
            member._in_guild = True
            member.kick_count -= 1
            await self.send_guild_log(
                guild=internal_guild,
                message=f"An error occurred trying to {'kick' if is_kick else 'ban'}: <@{member.id}>",
                delete_after_time=channel_delete_after,
            )
            log.warning(
                "An error occurred trying to %s: %s",
                {"kick" if is_kick else "ban"},
                member.id,
            )
            if sent_message is not None:
                if is_kick:
                    user_failed_message = self.transform_message(
                        self.handler.options.member_failed_kick_message,
                        original_message,
                        member.warn_count,
                        member.kick_count,
                    )
                else:
                    user_failed_message = self.transform_message(
                        self.handler.options.member_failed_ban_message,
                        original_message,
                        member.warn_count,
                        member.kick_count,
                    )

                await self.send_guild_log(
                    internal_guild,
                    user_failed_message,
                    channel_delete_after,
                    MockedChannel().to_mock(),
                )
                await sent_message.delete()

        else:
            await self.send_guild_log(
                guild=internal_guild,
                message=guild_message,
                delete_after_time=channel_delete_after,
                original_channel=MockedChannel().to_mock(),
            )

        member._in_guild = True
        await self.handler.cache.set_member(member)

    async def delete_messages(self, member: Member) -> None:
        bot = self.handler.bot
        channels = {}
        for message in member.messages:
            if message.is_duplicate:
                # cache channel for further fetches
                if message.channel_id not in channels:
                    channel = bot.get_channel(message.channel_id)
                    if not channel:
                        channel = await bot.fetch_channel(message.channel_id)

                    channels[message.channel_id] = channel
                else:
                    channel = channels[message.channel_id]

                actual_message = await channel.fetch_message(message.id)
                await self.delete_message(actual_message)

    async def delete_message(self, message: discord.Message) -> None:
        try:
            await message.delete()
            log.debug("Deleted message: %s", message.id)
        except discord.HTTPException:
            # Failed to delete message
            log.warning(
                "Failed to delete message %s in guild %s", message.id, message.guild.id
            )

    async def send_message_to_(
        self,
        target: discord.abc.Messageable,
        message: Union[str, discord.Embed],
        delete_after_time: Optional[int] = None,
    ) -> None:
        if isinstance(message, discord.Embed):
            await target.send(
                embed=message,
                delete_after=delete_after_time,
            )
        else:
            await target.send(
                message,
                delete_after=delete_after_time,
            )
