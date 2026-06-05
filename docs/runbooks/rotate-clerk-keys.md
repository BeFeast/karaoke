# Rotate Clerk keys

Karaoke reuses the scribe Clerk app. The karaoke Infisical path keeps a manual mirror of
the scribe Clerk values so the stack can boot from `services/prod/karaoke` without reading
scribe paths at runtime.

## Scope

- Infisical project: `services` (`5b5038c7-46d5-496f-bfa6-6184cb41e143`)
- Environment: `prod`
- Karaoke path: `/karaoke`
- Mirrored keys: `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWKS_ISSUER`

## Rotation procedure

1. Rotate or confirm the Clerk values in the shared scribe Clerk app.
2. Read the current scribe Infisical values from the existing scribe path.
3. Write those exact values into `services/prod/karaoke` for the three `CLERK_*` keys.
4. Verify the karaoke path returns the mirrored keys with non-empty values:

   ```bash
   curl "$INFISICAL_API_URL/v3/secrets/raw?workspaceId=5b5038c7-46d5-496f-bfa6-6184cb41e143&environment=prod&secretPath=/karaoke"
   ```

5. Restart the karaoke stack after Infisical Agent has rendered the updated environment.
6. Sign in through the shared scribe Clerk app and submit a karaoke job to verify auth.

Do not commit Clerk values, Infisical tokens, session cookies, or rendered `.env` files to
the repo.
