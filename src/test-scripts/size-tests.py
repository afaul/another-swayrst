import i3ipc


def print_widths(x, level=0):
    name = x.name
    width = x.rect.width
    height = x.rect.height
    id = x.id
    print(f"{'  '*level}{name}({id}): {width}x{height}")
    for node in x.nodes:
        print_widths(node, level=level + 1)


if __name__ == "__main__":

    x = i3ipc.Connection()
    print_widths(x.get_tree())
    con = x.get_tree().find_by_id(17)
    if con is not None:
        # a = con.command(f"resize grow right 50px")
        a = con.command(f"resize shrink down 50px")
        if not a[0].success:
            print(a[0].error)
    print_widths(x.get_tree())
