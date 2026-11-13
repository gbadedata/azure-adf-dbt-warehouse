-- Fails if any column in the marts schema looks like personal contact data.
-- Analysts are granted the marts schema only, so this is the line that keeps
-- email addresses (and their hashes) out of reporting.
select table_name, column_name
from information_schema.columns
where table_schema = '{{ ref("fct_donations").schema }}'
  and (
       lower(column_name) like '%email%'
    or lower(column_name) like '%phone%'
    or lower(column_name) like '%first_name%'
    or lower(column_name) like '%last_name%'
    or lower(column_name) like '%address%'
  )
