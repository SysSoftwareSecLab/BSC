#define LEFT 1
#define RIGHT 2
#define PAYLOAD_ALPHA 0
#define PAYLOAD_BETA 1
#define FIXTURE_ALPHA 0
#define TOOL_BETA 1
#define MODE_FAST 0
#define MODE_SAFE 1

byte ready;
byte mode;
byte holders[2];
byte authority[2];
byte sender[2];
byte resource_owner[2];
bool bad = false;

inline fail_now() {
    bad = true;
    assert(bad == false)
}

inline do_close(arm, object_id) {
    atomic {
        if
        :: holders[object_id] == 0 ->
            holders[object_id] = arm;
            authority[object_id] = arm;
            sender[object_id] = arm
        :: (holders[object_id] & arm) != 0 ->
            fail_now()
        :: else ->
            /* No-move subset: the frozen initial tools are outside the handover zone. */
            fail_now()
        fi
    }
}

inline do_open(arm, object_id) {
    atomic {
        if
        :: (holders[object_id] & arm) == 0 ->
            fail_now()
        :: holders[object_id] == arm ->
            holders[object_id] = 0;
            authority[object_id] = 0;
            sender[object_id] = 0
        :: else ->
            /* A dual grasp cannot be legal in this no-move spatial subset. */
            fail_now()
        fi
    }
}

inline do_transfer(object_id, from_arm, to_arm) {
    atomic {
        /* Any no-move transfer violates the frozen handover-zone obligation. */
        fail_now()
    }
}

inline do_acquire(arm, resource_id) {
    atomic {
        if
        :: resource_owner[resource_id] == 0 -> resource_owner[resource_id] = arm
        :: else -> fail_now()
        fi
    }
}

inline do_release(arm, resource_id) {
    atomic {
        if
        :: resource_owner[resource_id] == arm -> resource_owner[resource_id] = 0
        :: else -> fail_now()
        fi
    }
}

/* CASE-F4E62797B4F3293AACCD */
init {
    if
    :: mode = MODE_FAST
    :: mode = MODE_SAFE
    fi;
    if
    :: mode == MODE_FAST ->
        do_close(LEFT, PAYLOAD_ALPHA);
        skip;
        do_close(RIGHT, PAYLOAD_ALPHA);
        do_transfer(PAYLOAD_ALPHA, LEFT, RIGHT);
        do_open(LEFT, PAYLOAD_ALPHA);
    :: else ->
        do_close(RIGHT, PAYLOAD_BETA);
        skip;
        do_close(LEFT, PAYLOAD_BETA);
        do_transfer(PAYLOAD_BETA, RIGHT, LEFT);
        do_open(RIGHT, PAYLOAD_BETA);
    fi;
    assert(resource_owner[FIXTURE_ALPHA] == 0);
    assert(resource_owner[TOOL_BETA] == 0);
    assert(bad == false);
}
