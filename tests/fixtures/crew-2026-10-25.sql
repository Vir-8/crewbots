-- A crew's database as the first version of crewbots made it (25 October 2026): tasks and events only, before
-- chats, rooms, reviews, routines, search or any of the columns added since.
create table tasks (
    id            text primary key,
    bot           text not null,
    title         text not null,
    status        text not null,
    created_by    text not null,
    parent_id     text references tasks(id),
    workspace     text,
    repo          text,
    worktree_path text,
    branch        text,
    base          text,
    session_id    text,
    result        text,
    error         text,
    input_tokens  integer not null default 0,
    output_tokens integer not null default 0,
    cost_usd      real not null default 0,
    created_at    text not null,
    updated_at    text not null
);
create table events (
    id      integer primary key autoincrement,
    task_id text not null references tasks(id),
    kind    text not null,
    data    text not null,
    at      text not null
);
create index events_by_task on events(task_id, id);
insert into tasks values ('t_0000000001', 'chief', 'Add a --verbose flag', 'idle', 'user', null, '/work/app', null,
    null, null, null, 's-chief', 'Delegated it.', null, 1200, 80, 0.02, '2026-10-25T09:00:00.000+00:00',
    '2026-10-25T09:05:00.000+00:00');
insert into tasks values ('t_0000000002', 'engineer', 'Add the flag, with a test', 'idle', 'chief', 't_0000000001',
    '/work/app', '/work/app', '/home/.crewbots/worktrees/app/t_0000000002', 'crewbots/t_0000000002', 'a1b2c3d',
    's-eng', 'Done: --verbose prints each step.', null, 3400, 200, 0.05, '2026-10-25T09:01:00.000+00:00',
    '2026-10-25T09:04:00.000+00:00');
insert into events (task_id, kind, data, at) values ('t_0000000001', 'message',
    '{"from": "user", "text": "Add a --verbose flag to the CLI, with tests"}', '2026-10-25T09:00:00.000+00:00');
insert into events (task_id, kind, data, at) values ('t_0000000002', 'text',
    '{"text": "The verbose flag now prints each step as it runs."}', '2026-10-25T09:03:00.000+00:00');
insert into events (task_id, kind, data, at) values ('t_0000000002', 'usage',
    '{"input_tokens": 3400, "output_tokens": 200, "cached_input_tokens": 0, "cost_usd": 0.05}',
    '2026-10-25T09:03:30.000+00:00');
