"""WordPress-owned connection metadata; no transport or secret exposure here."""

from bukmatika.plugins.connection_contracts import (
    ConnectionField,
    ConnectionFieldKind,
    ConnectionMode,
    SourceConnectionContract,
)


WORDPRESS_CONNECTION = SourceConnectionContract(
    schema_version=1,
    modes=(
        ConnectionMode(
            mode_id="rest",
            display_name="WordPress REST API",
            fields=(
                ConnectionField("site_url", "Site URL", ConnectionFieldKind.URL),
                ConnectionField("username", "Application username", ConnectionFieldKind.TEXT),
                ConnectionField(
                    "application_password", "Application password", ConnectionFieldKind.SECRET
                ),
            ),
        ),
        ConnectionMode(
            mode_id="mysql",
            display_name="Direct MySQL (restricted administration)",
            fields=(
                ConnectionField("host", "Database host", ConnectionFieldKind.TEXT),
                ConnectionField("port", "Database port", ConnectionFieldKind.INTEGER),
                ConnectionField("database", "Database", ConnectionFieldKind.TEXT),
                ConnectionField("username", "Database username", ConnectionFieldKind.TEXT),
                ConnectionField("password", "Database password", ConnectionFieldKind.SECRET),
                ConnectionField("table_prefix", "WordPress table prefix", ConnectionFieldKind.TEXT),
            ),
        ),
    ),
)
