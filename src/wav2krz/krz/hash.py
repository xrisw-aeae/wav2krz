"""Kurzweil object hash generation."""


class KHash:
    """Hash/ID generation for Kurzweil objects."""

    # Object type constants
    T_PROGRAM = 36
    T_KEYMAP = 37
    T_SAMPLE = 38
    T_QABANK = 111
    T_SONG = 112
    T_EFFECT = 113

    @staticmethod
    def generate(obj_id: int, obj_type: int) -> int:
        """
        Generate a hash from object ID and type.

        For types <= 42: hash = (type << 10) + id
        For other types: more complex encoding (not commonly needed)

        Args:
            obj_id: Object ID (0-999 typically)
            obj_type: Object type constant

        Returns:
            Hash value for the object
        """
        if obj_type <= 42:
            return (obj_type << 10) + obj_id
        else:
            # Handle special types (effect, song, qabank)
            if obj_type == KHash.T_EFFECT:
                if obj_id < 100:
                    return (obj_type << 8) + obj_id
                else:
                    return (obj_type << 8) + ((obj_id // 100) * 10) + (obj_id % 100) + 28
            elif obj_type in (KHash.T_SONG, KHash.T_QABANK):
                if obj_id < 100:
                    return ((obj_type << 8) + obj_id) & 0xFFFF
                else:
                    return ((obj_type << 8) + (obj_id // 100) * 20 + (obj_id % 100) + 56) & 0xFFFF
            return (obj_type << 8) + obj_id

    @staticmethod
    def get_id(hash_val: int) -> int:
        """
        Extract object ID from hash.

        Args:
            hash_val: Hash value

        Returns:
            Object ID
        """
        if (hash_val & 0x8000) > 0:
            return hash_val & 1023

        obj_type = KHash.get_type(hash_val)
        if obj_type == KHash.T_EFFECT:
            low = hash_val & 255
            if low < 38:
                return low
            else:
                return ((low - 8) % 10) + 10 * (((low - 28) - ((low - 8) % 10)))
        elif obj_type in (KHash.T_SONG, KHash.T_QABANK):
            low = hash_val & 255
            return (1023 & (((low - 16) % 20) + 5 * (((low - 56) - ((low - 16) % 20)))))

        return hash_val & 255

    @staticmethod
    def get_type(hash_val: int) -> int:
        """
        Extract object type from hash.

        Args:
            hash_val: Hash value

        Returns:
            Object type constant
        """
        if (hash_val & 0x8000) > 0:
            return hash_val >> 10
        return hash_val >> 8
