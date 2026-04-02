import six


def build_where_clause(**kwargs):
	where_clause = ""

	if len(kwargs) > 0:
		where = []

		for key, value in kwargs.items():
			if isinstance(value, six.string_types):
				where.append("{} = '{}'".format(key, value.replace("'", "'")))
			else:
				where.append(f"{key} = {value}")

		where_clause = " AND ".join(where)

	return where_clause


def build_choose_clause(choices, field):
	where_clause = ""

	if len(choices) > 0:
		where = []

		for choice in choices:
			if isinstance(choice, six.string_types):
				where.append("'{}'".format(choice.replace("'", "'")))
			else:
				where.append(f"{choice}")

		where_clause = ", ".join(where)
		where_clause = f"{field} in ({where_clause})"

	return where_clause
