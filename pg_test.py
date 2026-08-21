import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect('postgresql://postgres:postgres@localhost:5432/postgres')
    
    await conn.execute("DROP TABLE IF EXISTS payments CASCADE")
    await conn.execute("DROP TABLE IF EXISTS users CASCADE")
    
    await conn.execute('''
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            referred_by BIGINT,
            is_deleted BOOLEAN DEFAULT false,
            is_banned BOOLEAN DEFAULT false
        )
    ''')
    await conn.execute('''
        CREATE TABLE payments (
            id SERIAL PRIMARY KEY,
            user_id INT,
            amount DECIMAL,
            currency VARCHAR(20),
            provider_status VARCHAR(20),
            fulfillment_status VARCHAR(20),
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            public_order_id VARCHAR(100),
            external_id VARCHAR(100),
            provider_idempotency_key VARCHAR(100),
            provider_confirmed_at TIMESTAMP,
            topup_context JSONB
        )
    ''')
    
    await conn.execute('''
        CREATE INDEX ix_payments_referral_bonus_unprocessed ON payments (created_at) 
        WHERE provider_status = 'succeeded' AND fulfillment_status = 'succeeded' AND NOT (COALESCE(topup_context, '{}'::jsonb) @> '{"referral_bonus_processed": true}'::jsonb);
    ''')
    await conn.execute('''
        CREATE INDEX ix_payments_recovery_pending ON payments (created_at) 
        WHERE external_id IS NOT NULL AND provider_status IN ('creating', 'pending', 'waiting_for_capture', 'unknown');
    ''')
    await conn.execute('''
        CREATE INDEX ix_payments_recovery_unfulfilled ON payments (created_at) 
        WHERE provider_status = 'succeeded' AND provider_confirmed_at IS NOT NULL AND fulfillment_status NOT IN ('succeeded', 'reversed', 'manual_review');
    ''')
    
    await conn.execute("INSERT INTO users (telegram_id) VALUES (1)")
    await conn.execute('''
        INSERT INTO payments (user_id, amount, currency, provider_status, fulfillment_status, created_at, updated_at, public_order_id, external_id, provider_idempotency_key, topup_context) 
        SELECT 1, 100, 'RUB', 'succeeded', 'succeeded', now(), now(), md5(random()::text), md5(random()::text), md5(random()::text), '{"referral_bonus_processed": true}'::jsonb 
        FROM generate_series(1, 5000)
    ''')
    
    await conn.execute("ANALYZE payments")
    await conn.execute("ANALYZE users")
    
    query = '''
    EXPLAIN SELECT 1 FROM payments 
    WHERE 
        (external_id IS NOT NULL AND provider_status IN ('creating', 'pending', 'waiting_for_capture', 'unknown'))
        OR 
        (provider_status = 'succeeded' AND provider_confirmed_at IS NOT NULL AND fulfillment_status NOT IN ('succeeded', 'reversed', 'manual_review'))
        OR 
        (provider_status = 'succeeded' AND provider_confirmed_at IS NOT NULL AND fulfillment_status = 'succeeded' AND EXISTS(SELECT 1 FROM users u1 JOIN users u2 ON u1.referred_by = u2.telegram_id) AND NOT (COALESCE(topup_context, '{}'::jsonb) @> '{"referral_bonus_processed": true}'::jsonb))
    '''
    
    res = await conn.fetch(query)
    for r in res:
        print(r[0])
    
    await conn.close()

asyncio.run(main())
