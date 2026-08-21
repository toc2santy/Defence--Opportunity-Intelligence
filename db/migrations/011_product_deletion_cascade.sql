-- ============================================================
-- Fixes a real gap found while building product deletion:
-- product_capabilities already cascades correctly when a product
-- is deleted, but opportunities never got the same treatment —
-- meaning deleting a product with existing matches would have
-- failed with a foreign key violation instead of working cleanly.
--
-- If a product is deleted (e.g. because it was entered with wrong
-- data), its derived opportunities are meaningless too, so cascade
-- is the correct behavior here, matching product_capabilities.
--
-- Apply the same way as previous migrations:
--   docker compose exec -T db psql -U postgres -d doi < db/migrations/011_product_deletion_cascade.sql
-- ============================================================

alter table opportunities drop constraint if exists opportunities_product_id_fkey;
alter table opportunities add constraint opportunities_product_id_fkey
    foreign key (product_id) references products(id) on delete cascade;
