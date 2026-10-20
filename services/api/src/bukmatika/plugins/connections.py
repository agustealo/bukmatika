from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import DatabaseSecretStore, InstallationCredentialKey, SecretOwner
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.plugins.connection_contracts import ConnectionFieldKind
from bukmatika.plugins.contracts import PluginRegistry
from bukmatika.plugins.installations import PluginInstallationRepository


class PluginConnectionInvalid(ValueError):
    pass


class PluginConnectionRepository:
    """Persist source-owned settings, with secrets stored only in the encrypted vault."""

    def __init__(
        self,
        session: AsyncSession,
        registry: PluginRegistry,
        *,
        installation_key: InstallationCredentialKey,
    ) -> None:
        self._session = session
        self._registry = registry
        self._installations = PluginInstallationRepository(session, registry)
        self._secrets = DatabaseSecretStore(session, installation_key=installation_key)

    async def configure(
        self,
        *,
        principal_id: UUID,
        plugin_id: str,
        mode_id: str,
        values: dict[str, str | int],
    ) -> PluginConnection:
        registration = self._registry.registration(plugin_id)
        contract = registration.connection_contract
        if contract is None:
            raise PluginConnectionInvalid("Plugin has no connection contract")
        mode = next((item for item in contract.modes if item.mode_id == mode_id), None)
        if mode is None:
            raise PluginConnectionInvalid("Unsupported connection mode")
        expected = {field.key: field for field in mode.fields}
        if set(values) != set(expected):
            raise PluginConnectionInvalid("Connection settings must match the declared fields")
        for key, field in expected.items():
            value = values[key]
            if field.kind is ConnectionFieldKind.INTEGER:
                if type(value) is not int or value < 1 or value > 65535:
                    raise PluginConnectionInvalid("Invalid connection port")
            elif not isinstance(value, str) or not value.strip():
                raise PluginConnectionInvalid("Invalid connection setting")
            if field.kind is ConnectionFieldKind.URL and not str(value).startswith("https://"):
                raise PluginConnectionInvalid("Connection URL must use HTTPS")

        installation = await self._installations.require(
            principal_id=principal_id, plugin_id=plugin_id
        )
        row = await self._session.scalar(
            select(PluginConnection).where(
                PluginConnection.installation_id == installation.id,
                PluginConnection.principal_id == principal_id,
                PluginConnection.plugin_id == plugin_id,
            )
        )
        if row is None:
            row = PluginConnection(
                installation_id=installation.id,
                principal_id=principal_id,
                plugin_id=plugin_id,
                mode_id=mode_id,
                schema_version=contract.schema_version,
                settings={},
            )
            self._session.add(row)
            await self._session.flush()
        else:
            # Mode changes invalidate all previous connection-owned credentials.
            await self._secrets.delete_owner(
                principal_id=principal_id,
                owner_kind="plugin_connection",
                owner_id=row.id,
            )
        row.mode_id = mode_id
        row.schema_version = contract.schema_version
        row.settings = {
            key: value
            for key, value in values.items()
            if expected[key].kind is not ConnectionFieldKind.SECRET
        }
        for key, field in expected.items():
            if field.kind is ConnectionFieldKind.SECRET:
                secret_value = values[key]
                assert isinstance(secret_value, str)
                await self._secrets.replace(
                    owner=SecretOwner(
                        principal_id=principal_id,
                        owner_kind="plugin_connection",
                        owner_id=row.id,
                        slot=key,
                    ),
                    secret=secret_value,
                )
        await self._session.flush()
        return row
